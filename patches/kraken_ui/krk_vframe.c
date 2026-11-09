/*
 * krk_vframe.c - voice-frame I/O layer; see krk_vframe.h for the design and the threading rules.
 *
 * Splice sites (all in the IRAM load image, flash sector 0x030DE000 = IRAM + 0x030afc20; a `bsr` there
 * is encoded against the IRAM pc, so the manifest targets are "<symbol> + 0x30afc20"):
 *   TX  IRAM 0x2e9ea, 0x2eaf0 (live), 0x2e6c0, 0x2e820 (dead variant, hooked for completeness)
 *       stock `bsr 0x172f8` with r0 = 0, r1 = 0x49b18, r2 = 27 -> bsr krk_vf_tx_hook
 *   RX  IRAM 0x2ed64   stock `bsr 0x172cc` with r0 = 0, r1 = 0x46b38, r2 = 27 -> bsr krk_vf_rx_hook
 * Registers: 0x172f8/0x172cc clobber r0-r3 and r12; the code after the sites uses only r5, r6, r8, r11
 * (callee-saved, kept by the C ABI) and reloads r2, r3, r12, r13 before use (docs/parrot-tx.md 2.2,
 * re-checked against the disassembly).
 */
#include "krk_vframe.h"
#include "krk_stock.h"

#define stock_172f8 ((void (*)(u32 off, const u8 *src, u32 n))0x000172f8)   /* copy to modem window 0x16000960 */
#define stock_172cc ((void (*)(u32 off, u8 *dst, u32 n))0x000172cc)         /* copy from it */
#define TX_BUF      0x00049b18u     /* encoder output, argument r1 of the TX call sites */
#define VF_MAGIC    0x56463031u     /* "VF01": patch SRAM holds noise at power-up */
#define VF_GRACE    100             /* bursts of silence after the end before the layer gives up (6 s) */
#define NOEND       0xffffffffu

extern void krk_scramble_tx_hook(void);        /* krk_scramble.c */

struct krk_vf krk_vf KRK_FEAT;
static volatile u8 big_owner KRK_FEAT;
static u8 big_buf[KRK_VF_BIG_BYTES] __attribute__((section(".bss.big"), aligned(4)));

/*
 * Silence burst: three copies of the 9-byte AMBE+2 "null" frame used by MMDVMHost / DMR gateways
 * (NULL_AMBE = B9 E8 81 52 61 73 00 2A 6B, the over-the-air bit layout of a frame that decodes to
 * comfort silence). UNVERIFIED for this modem: the window is believed to carry the same MSB-first
 * 72-bit frames (docs/parrot-tx.md 2.3) but no silence capture exists yet. Hardware round: the self-
 * check dumps a stock burst recorded in a quiet room next to this pattern; replace it if they differ.
 */
static const u8 silence_burst[KRK_VF_LEN] = {
    0xB9, 0xE8, 0x81, 0x52, 0x61, 0x73, 0x00, 0x2A, 0x6B,
    0xB9, 0xE8, 0x81, 0x52, 0x61, 0x73, 0x00, 0x2A, 0x6B,
    0xB9, 0xE8, 0x81, 0x52, 0x61, 0x73, 0x00, 0x2A, 0x6B,
};

u32 krk_vf_ticks(void)
{
    return OS_TICKS;
}

void krk_vf_silence(u8 *dst)
{
    int i;

    for (i = 0; i < KRK_VF_LEN; i++)
        dst[i] = silence_burst[i];
}

void krk_vf_release(void)
{
    OSFlagPost_wrapper_set(PTT_GROUP, PTT_RELEASE);
    krk_vf.rel_posts++;
}

void krk_vf_press(void)
{
    OSFlagPost_wrapper_set(PTT_GROUP, PTT_PRESS);
}

/* Init on first use, and again after a warm reset (the tick counter restarts at 0, so it is below the
 * stamp of the previous run): that drops any session and sink left in SRAM. */
void krk_vf_service(void)
{
    struct krk_vf *v = &krk_vf;
    u32 now = OS_TICKS;

    if (v->magic != VF_MAGIC || krk_rebooted(&v->stamp)) {
        int i;
        u8 *p = (u8 *)v;

        v->flags = 0;
        v->rx_sink = 0;
        for (i = 0; i < (int)sizeof *v; i++)
            p[i] = 0;
        big_owner = 0;
        v->end_idx = NOEND;
        v->magic = VF_MAGIC;
    }
    v->stamp = now;
}

u8 *krk_vf_big_claim(u8 owner)
{
    if (big_owner && big_owner != owner)
        return 0;
    big_owner = owner;
    return big_buf;
}

void krk_vf_big_release(u8 owner)
{
    if (big_owner == owner)
        big_owner = 0;
}

void krk_vf_tx_begin(krk_vf_tx_fn fn, u32 max_bursts, u32 max_ticks, int tap)
{
    struct krk_vf *v = &krk_vf;

    krk_vf_service();
    v->flags = 0;                   /* hooks pass the stock bytes through while we fill the session */
    v->done = 0;
    v->quiet = 0;
    v->tap_n = 0;
    v->tx_idx = 0;
    v->end_idx = NOEND;
    v->tx_fn = fn;
    v->max_bursts = max_bursts;
    v->max_ticks = max_ticks;
    v->t_begin = OS_TICKS;
    v->flags = (u8)((fn ? KRK_VF_SUBST : 0) | (tap ? KRK_VF_TAPON : 0));
}

void krk_vf_tx_end(void)
{
    krk_vf.quiet = 0;
    krk_vf.flags = 0;
}

/* Review fix 7: after an abort / unkey / stuck decision a press may still be in flight, so TX voice can come up
 * later. Keep substituting, but with silence only: max_bursts = 0 makes the very next burst the end (silence +
 * release, then silence and a release every 8th burst), and `quiet` stops the grace/age limits from handing the
 * microphone back while TX voice is up (tx_session). Only a session in progress is touched. */
void krk_vf_tx_quiet(void)
{
    struct krk_vf *v = &krk_vf;

    if (!(v->flags & KRK_VF_SUBST))
        return;
    if (!v->quiet) {
        v->t_quiet = OS_TICKS;
        v->quiet = 1;
    }
    v->max_bursts = 0;
}

void krk_vf_rx_sink(krk_vf_rx_fn fn)
{
    krk_vf_service();
    krk_vf.rx_sink = fn;
}

/* Slow path: only runs inside a session (flags != 0). */
static void __attribute__((noinline)) tx_session(struct krk_vf *v, u8 *f)
{
    u32 i = v->tx_idx++;
    int r, k;

    if (v->quiet) {                             /* silence-only: stays until the owner ends it or 30 s */
        if (OS_TICKS - v->t_quiet >= 3000u) {      /* (also true when the counter restarted: stale after a reset) */
            v->quiet = 0;
            v->flags = 0;
            v->done = 1;
            return;
        }
    } else if (OS_TICKS - v->t_begin >= v->max_ticks) {
        if ((v->flags & KRK_VF_SUBST) && OS_TICKS >= v->t_begin && (TXUP & 1)) {
            v->t_quiet = OS_TICKS;                 /* genuinely over time with TX still up: silence, not the microphone */
            v->quiet = 1;
            v->max_bursts = 0;
        } else {                                /* too old (or stale after a reset): never hijack a later TX */
            v->flags = 0;
            v->done = 1;
            return;
        }
    }
    if ((v->flags & KRK_VF_TAPON) && v->tap_n < KRK_VF_TAP) {
        u8 *t = v->tap[v->tap_n];

        for (k = 0; k < KRK_VF_LEN; k++)
            t[k] = f[k];
        v->tap_n++;
    }
    if (!(v->flags & KRK_VF_SUBST))
        return;
    if (i >= v->end_idx) {                      /* past the end: silence, keep asking for the release */
        k = (int)(i - v->end_idx);
        krk_vf_silence(f);
        v->tx_silence++;
        if ((k & 7) == 0)
            krk_vf_release();
        if (k >= VF_GRACE && !(v->quiet && (TXUP & 1))) {
            v->quiet = 0;
            v->flags = 0;
            v->done = 1;
        }
        return;
    }
    r = i >= v->max_bursts ? KRK_VF_NONE : v->tx_fn(f, i);
    if (r == KRK_VF_FRAME) {
        v->tx_subst++;
    } else if (r == KRK_VF_LAST) {
        v->tx_subst++;
        v->end_idx = i + 1;
        krk_vf_release();                       /* burst-synchronous unkey: this is the last burst */
    } else if (r == KRK_VF_PASS) {
        v->tx_pass++;
    } else {                                    /* provider exhausted (or cap hit): silence + release */
        krk_vf_silence(f);
        v->tx_silence++;
        v->end_idx = i + 1;
        krk_vf_release();
    }
}

/* Replaces `bsr 0x172f8` (r0 = 0, r1 = 0x49b18, r2 = 27) in the TX task. Idle cost: one load+compare
 * (magic), one counter increment, one load+test (flags), then the stock call. */
void krk_vf_tx_hook(u32 off, u8 *src, u32 n)
{
    struct krk_vf *v = &krk_vf;

    krk_scramble_tx_hook();             /* F12: SCRAMBLE_REG is right before the first burst of a transmission */
    if (v->magic == VF_MAGIC) {
        v->tx_calls++;
        if (v->flags && n == KRK_VF_LEN && (u32)src == TX_BUF)
            tx_session(v, src);
    }
    stock_172f8(off, src, n);
}

/* Replaces `bsr 0x172cc` (r0 = 0, r1 = 0x46b38, r2 = 27) in the RX task: stock copy first, then the sink. */
void krk_vf_rx_hook(u32 off, u8 *dst, u32 n)
{
    struct krk_vf *v = &krk_vf;
    krk_vf_rx_fn fn;

    stock_172cc(off, dst, n);
    if (v->magic == VF_MAGIC) {
        v->rx_calls++;
        fn = v->rx_sink;
        if (fn && n == KRK_VF_LEN) {
            fn(dst);
            v->rx_sunk++;
        }
    }
}
