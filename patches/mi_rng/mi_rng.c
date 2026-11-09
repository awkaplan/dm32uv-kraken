/*
 * mi_rng.c -- replaces the DMR-privacy message-indicator (MI) source: libc rand() -> an
 * AES-256 CTR_DRBG seeded from real hardware noise. See docs/encryption.md for the flaw this
 * fixes (only 512 distinct initial MIs, identical on every boot) and README.md for the design.
 *
 * HOOK: one call site. dmr_privacy_key_load_and_mi_draw @0x0305ca48 fills the 6 MI bytes of the TX
 * context with
 *     0x0305caec  bsr lcg_rand_next ; st.b r0,(r5,0) ; addi r5,1 ; cmpne r5,r4 ; bt 0x0305caec
 * The manifest rewrites that one `bsr` to call mi_rng_entry, which returns one random byte in
 * r0 (the caller stores the low byte, as before). Nothing else in stock is touched, and
 * lcg_rand_next keeps serving its other callers (ALPU exchange, scan jitter).
 *
 * CONSTRUCTION (NIST SP 800-90A CTR_DRBG shape, AES-256, no derivation function; NOT claimed to
 * be a validated implementation):
 *   state   K[32], V[16]
 *   update(P[48]):  T = E_K(V+1) | E_K(V+2) | E_K(V+3);  T ^= P;  K = T[0:32];  V = T[32:48]
 *   generate:       V += 1;  out = E_K(V);  update(0)     (ratchet: output never recoverable
 *                                                          from a later state compromise)
 * Every generate is preceded by update(fresh noise), so the state never runs on its seed alone.
 *
 * AES is the firmware's OWN routine, run from its IRAM image (aes_key_schedule_expand /
 * aes_block_encrypt, KAT-verified against FIPS-197 by crypto_kat.py).
 * The encrypt routine works in a GLOBAL state buffer (IRAM 0x33c88), so it is not reentrant:
 * the DMR TX/RX keystream code uses the same buffer. Every encrypt below therefore runs with
 * IRQs off (stock's own CPU_SR_Save/Restore pair), which keeps a tick or modem interrupt from
 * switching to another AES user mid-block. The key schedule writes only to our buffer.
 *
 * NOISE (noise_collect): per round, four conversions on ADC channels 1,2,1,2 with the stock
 * reader (only ADC1/ADC2 are muxed to the ADC on stock: adc_stop_and_reset @0x0301caec clears
 * IO_DIPLEX2 bits 20/21 and nothing clears 19 or 22-26, so other channels read a GPIO constant),
 * each sample stamped with the 84 MHz free-running TIMER0 down-counter before and after. The
 * stamps carry the phase between the ADC's clock domain and the CPU, which is the part an
 * attacker cannot replay. A round's 32 bytes are one Davies-Meyer step over AES-256
 * (h = E_blk(h) ^ h), then a three-block finalisation gives the 48 bytes update() wants.
 * ENTROPY RATE IS UNMEASURED. The ADC low bits and timer phase are plausible sources, not
 * characterised ones; SEED_ROUNDS is generous for that reason. See README.md "What is not proven".
 *
 * SEEDING: the first call after power-up (state magic absent) collects SEED_ROUNDS rounds. That
 * call is the boot-time initial channel load: radio_boot_full_init_sequence reads the ADC
 * (@0x0305f3e2) and has started the timers (@0x0305f2e0) before channel_change_apply_with_zone_
 * and_flash_validate (@0x0305f436), so the ADC and its semaphore are live. Seeding on first use
 * rather than from a separate boot hook costs nothing: the draw is the only consumer.
 *
 * PER-UNIT MIX: the seed also absorbs the first 0x200 bytes of the per-unit parameter sector
 * (flash 0x0300b000, XIP read only: the eFuse-derived binding record at +0x10c, hardware bytes
 * at +0x80/+0xa0, per-unit strings). It is unique per radio but NOT secret and NOT random; it
 * only keeps two radios with poor noise from sharing a sequence. Shipped images have it blank.
 *
 * LOCKING: the static state is reachable from more than one task (boot init task and a
 * channel-switch task, both via channel_change_apply_dispatcher, with no stock lock on the zone
 * path). A busy word is taken with IRQs off; a contending caller sleeps 1 ms (OSTimeDly) and
 * retries, bounded, then proceeds regardless (a stuck owner must not wedge channel changes).
 *
 * ASSUMPTIONS (checked statically, not by running on a radio):
 *   - task context only (adc_read_channel_dispatch pends a semaphore forever, OSTimeDly sleeps);
 *     every traced root is a task, two roots are unresolved (README "What is not proven").
 *
 * No address appears here except the TIMER0 MMIO word (hr_c7000_regs.h, HRC_TIMER_BASE + 4);
 * stock functions come from tools/link/dm32_stock_symbols.ld, and the two AES entry points
 * are re-based to their IRAM execution addresses in mi_rng.ld.
 */
typedef unsigned char u8;
typedef unsigned int u32;

/* ---- stock functions (addresses supplied by the linker script) ---- */
extern void mi_aes_key_schedule(const u8 *key, u8 *sched, u32 nk, u32 nr);
extern void mi_aes_encrypt(const u8 *in, u8 *out, const u8 *sched, u32 nr);
extern u32 adc_read_channel_dispatch(u32 channel);       /* 10-bit result; blocks on a semaphore */
extern u32 cpu_sr_save_and_disable_irq_iram(void);       /* returns the old PSR */
extern void cpu_sr_restore_irq_iram(u32 psr);
extern void OSTimeDly_ms_wrapper(u32 ms);

#define NK 8u                        /* AES-256: 8 key words ... */
#define NR 14u                       /* ... 14 rounds */
#define MAGIC 0x4D495247u            /* "MIRG": state initialised */
#define SEED_ROUNDS 64u              /* 64 rounds x 4 ADC samples = 256 samples at first draw */
#define MIX_ROUNDS 2u                /* 8 samples folded in before every generate */
#define SAMPLES_PER_ROUND 4u
#define UNIT_SECTOR ((const u8 *)0x0300b000u)   /* per-unit parameter sector, read only */
#define UNIT_BYTES 0x200u
#define LOCK_TRIES 2000u             /* x 1 ms */
#define TIMER0_CURRENT (*(volatile const u32 *)0x14000004u) /* TIMER CH[0].CurrentValue */

/* SAMPLE TAP (measurement build only, mi_rng_tap.patch.json, -DMI_RNG_SAMPLE_TAP): every raw
 * 8-byte noise record is also appended to a ring buffer in its own SRAM claim, so a live-RAM
 * capture (dm32_ramdump.py) can bring the samples to a host for mi_rng_entropy.py. The first
 * draw collects MI_RNG_TAP_ROUNDS rounds instead of SEED_ROUNDS so one boot fills the buffer.
 * The generator itself is unchanged, but this build is for MEASURING, not for use: the buffer
 * is a plaintext copy of the seed material. Never leave it on a radio. */
#ifdef MI_RNG_SAMPLE_TAP
#ifndef MI_RNG_TAP_ROUNDS
#define MI_RNG_TAP_ROUNDS 253u       /* 253 x 4 seed + 2 x 4 first mix = 1020 records; buffer holds 1022 */
#endif
#define TAP_MAGIC 0x5054494Du        /* "MITP" */
#define TAP_CAPACITY ((8192u - 16u) / 8u)
struct mi_rng_tap {
    u32 magic;
    u32 count;                       /* records ever written (ring: the last TAP_CAPACITY are kept) */
    u32 capacity;
    u32 rounds;                      /* MI_RNG_TAP_ROUNDS, so the host knows the seed size */
    u8 rec[TAP_CAPACITY][8];
};
struct mi_rng_tap mi_rng_tap __attribute__((section(".bss.mi_rng_tap"), aligned(4)));

static void tap_record(const u8 *p)
{
    struct mi_rng_tap *t = &mi_rng_tap;
    u32 i;
    if (t->magic != TAP_MAGIC) {
        t->magic = TAP_MAGIC;
        t->count = 0;
        t->capacity = TAP_CAPACITY;
        t->rounds = MI_RNG_TAP_ROUNDS;
    }
    for (i = 0; i < 8; i++)
        t->rec[t->count % TAP_CAPACITY][i] = p[i];
    t->count++;
}
#define FIRST_SEED_ROUNDS MI_RNG_TAP_ROUNDS
#else
#define tap_record(p) ((void)0)
#define FIRST_SEED_ROUNDS SEED_ROUNDS
#endif

struct mi_rng {
    u32 magic;
    u32 avail;                       /* unread bytes left in buf */
    u32 calls;
    u32 busy;                        /* lock word, see header */
    u32 contended;                   /* times a caller found it busy (diagnostic) */
    u8 key[32];
    u8 v[16];
    u8 buf[16];
    u8 sched[240];                   /* 15 round keys */
    u8 temp[48];                     /* scratch lives here, not on the (unknown-size) task stack */
    u8 noise[48];
    u8 blk[32];
    u8 h[16];
} __attribute__((aligned(4)));

/* Global so the QEMU test and gdb_rsp.py can find it by name. Not cleared at boot: hardware
 * SRAM holds power-on noise there, which is why validity is an exact magic, not "non-zero". */
struct mi_rng mi_rng_state;

static const char iv_tag[16] = "DM32 MI RNG v2\0";
static const u8 adc_channel[SAMPLES_PER_ROUND] = {1, 2, 1, 2};

static void wipe(volatile u8 *p, u32 n)
{
    while (n--)
        *p++ = 0;
}

/* The firmware's AES block encrypt, serialised against every other AES user (see header). */
static void aes_block(const struct mi_rng *s, const u8 *in, u8 *out)
{
    u32 psr = cpu_sr_save_and_disable_irq_iram();
    mi_aes_encrypt(in, out, s->sched, NR);
    cpu_sr_restore_irq_iram(psr);
}

static void inc_v(u8 *v)
{
    int i;
    for (i = 15; i >= 0; i--)
        if (++v[i])
            break;
}

/* CTR_DRBG_Update. provided == 0 means 48 zero bytes. */
static void drbg_update(struct mi_rng *s, const u8 *provided)
{
    u32 i;
    mi_aes_key_schedule(s->key, s->sched, NK, NR);
    for (i = 0; i < 3; i++) {
        inc_v(s->v);
        aes_block(s, s->v, s->temp + 16 * i);
    }
    if (provided)
        for (i = 0; i < 48; i++)
            s->temp[i] ^= provided[i];
    for (i = 0; i < 32; i++)
        s->key[i] = s->temp[i];
    for (i = 0; i < 16; i++)
        s->v[i] = s->temp[32 + i];
    wipe(s->temp, sizeof s->temp);
}

/* One 16-byte output block into buf, then ratchet. */
static void drbg_generate(struct mi_rng *s)
{
    mi_aes_key_schedule(s->key, s->sched, NK, NR);
    inc_v(s->v);
    aes_block(s, s->v, s->buf);
    drbg_update(s, 0);
    s->avail = 16;
}

/* Davies-Meyer over AES-256: h = E_m(h) ^ h, with the 32-byte message m as the AES key. */
static void dm_compress(struct mi_rng *s, u8 *h, const u8 *m)
{
    u8 t[16];
    u32 i;
    mi_aes_key_schedule(m, s->sched, NK, NR);
    aes_block(s, h, t);
    for (i = 0; i < 16; i++)
        h[i] ^= t[i];
}

/* Gather `rounds` rounds of ADC + timer samples into s->noise[48]. */
static void noise_collect(struct mi_rng *s, u32 rounds)
{
    u32 r, ch, i;
    u8 *h = s->h, *blk = s->blk;

    for (i = 0; i < 16; i++)
        h[i] = (u8)iv_tag[i];

    for (r = 0; r < rounds; r++) {
        for (ch = 0; ch < SAMPLES_PER_ROUND; ch++) {
            u32 t0 = TIMER0_CURRENT;
            u32 v = adc_read_channel_dispatch(adc_channel[ch]);
            u32 t1 = TIMER0_CURRENT;
            u32 d = t0 - t1;                    /* conversion time in 84 MHz ticks (wraps at reload) */
            u8 *p = blk + 8 * ch;
            p[0] = (u8)t1;
            p[1] = (u8)(t1 >> 8);
            p[2] = (u8)(t1 >> 16);
            p[3] = (u8)(t1 >> 24);
            p[4] = (u8)v;
            p[5] = (u8)(v >> 8);
            p[6] = (u8)d;
            p[7] = (u8)(d >> 8);
            tap_record(p);
        }
        dm_compress(s, h, blk);
    }

    /* Per-unit sector, 16 blocks of 32 bytes (see header). */
    if (rounds > MIX_ROUNDS)
        for (r = 0; r < UNIT_BYTES; r += 32) {
            for (i = 0; i < 32; i++)
                blk[i] = UNIT_SECTOR[r + i];
            dm_compress(s, h, blk);
        }

    /* Finalise: noise[16j..] = DM(h, h || (j+1) x16) for j = 0..2. */
    for (r = 0; r < 3; r++) {
        u8 *out = s->noise + 16 * r;
        for (i = 0; i < 16; i++) {
            blk[i] = h[i];
            blk[16 + i] = (u8)(r + 1);
            out[i] = h[i];
        }
        dm_compress(s, out, blk);
    }
}

/* update(noise) then generate. Exported (and KEEP'd by the linker script) so the QEMU test can
 * feed a known noise vector and compare the output block against an independent model. */
__attribute__((section(".text.mi_rng_step")))
void mi_rng_step(struct mi_rng *s, const u8 *noise48)
{
    drbg_update(s, noise48);
    drbg_generate(s);
}

static void lock(struct mi_rng *s)
{
    u32 tries = 0;
    for (;;) {
        u32 psr = cpu_sr_save_and_disable_irq_iram();
        u32 was = s->busy;
        s->busy = 1;
        cpu_sr_restore_irq_iram(psr);
        if (!was || s->magic != MAGIC || ++tries > LOCK_TRIES)
            return;
        s->contended++;
        OSTimeDly_ms_wrapper(1);
    }
}

/* The call-site replacement: returns one random byte in r0. */
__attribute__((section(".text.mi_rng_entry")))
u32 mi_rng_entry(void)
{
    struct mi_rng *s = &mi_rng_state;
    u32 i, b;

    lock(s);
    if (s->magic != MAGIC) {                    /* first draw since power-up: heavy seed */
        for (i = 0; i < 32; i++)
            s->key[i] = 0;
        for (i = 0; i < 16; i++)
            s->v[i] = 0;
        noise_collect(s, FIRST_SEED_ROUNDS);
        drbg_update(s, s->noise);
        s->avail = 0;
        s->calls = 0;
        s->contended = 0;
        s->busy = 1;
        s->magic = MAGIC;
    }
    if (s->avail == 0 || s->avail > 16) {       /* the > 16 case is a false-positive magic */
        noise_collect(s, MIX_ROUNDS);
        mi_rng_step(s, s->noise);
    }
    s->calls++;
    b = s->buf[--s->avail];
    s->buf[s->avail] = 0;                       /* a byte is handed out once */
    s->busy = 0;
    return b;
}
