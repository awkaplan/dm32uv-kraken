/* Kraken reader: a small RAM program that the HR_C7000's mask boot ROM loads into IRAM and starts, so
 * the web installer can read the radio's program flash (docs/web-flasher.md).
 *
 * Written for this project. It only ever READS program flash, through the execute-in-place window at
 * 0x03000000, and talks on UART0 in the Kraken reader protocol (web/lib/kr.js has the host side):
 *
 *   frame = 5A A5 <type> <len u16 LE> <payload> <CRC-32 LE of type, len and payload>
 *   PING 01 -> PONG 81 (echo) | BAUD 02 <div u16> <ms u16> -> OK 82, switch, wait for a PING at the new
 *   rate, else go back | READ 03 <addr> <len> -> DATA 83 <addr> <4 KB>... | SUMS 04 <addr> <len> <step>
 *   -> SUMS 84 <CRC-32 per step> | anything else -> ERR 8F <code>. HELLO 80 "KRD" <ver> <div u16> at start.
 *
 * In boot-ROM mode the chip runs from its 24 MHz oscillator, no watchdog runs, the cache is off and the
 * XIP window reads without any setup (dm32uv-re notes on the mask ROM). The ROM uses no RAM, so the
 * image at 0x00010000 and our stack below 0x00020000 clobber nothing. Never store to 0x03xxxxxx: stores
 * there become SPI flash commands.
 *
 * No interrupts, no libc. It never returns: power the radio off to leave it.
 */
#include <stdint.h>

#define VERSION 1

#define FLASH_BASE 0x03000000u
#define FLASH_SIZE 0x00100000u

/* UART0: DesignWare APB UART, 16550 registers, 32-bit stride (dm32uv-re specs/dts/hr_c7000.dtsi) */
#define UART0 0x14030000u
#define UREG(off) (*(volatile uint32_t *)(UART0 + (off)))
#define U_RBR 0x00 /* receive buffer (read), THR (write), DLL when LCR.DLAB */
#define U_THR 0x00
#define U_DLL 0x00
#define U_DLH 0x04 /* when LCR.DLAB; IER otherwise */
#define U_IER 0x04
#define U_FCR 0x08 /* write */
#define U_IIR 0x08 /* read */
#define U_LCR 0x0c
#define U_LSR 0x14
#define U_USR 0x7c /* DesignWare: bit 0 BUSY */

#define LCR_DLAB 0x80
#define LSR_DR 0x01
#define LSR_THRE 0x20
#define LSR_TEMT 0x40


enum { T_PING = 0x01, T_BAUD = 0x02, T_READ = 0x03, T_SUMS = 0x04,
       T_HELLO = 0x80, T_PONG = 0x81, T_OK = 0x82, T_DATA = 0x83, T_SUMSR = 0x84, T_ERR = 0x8f };
enum { E_FRAME = 1, E_RANGE = 2, E_TYPE = 3 };

#define CHUNK 0x1000u
#define MAX_PAYLOAD 64u
#define MAX_SUMS 1024u

static uint32_t crc_table[256];
static int tx_burst = 1; /* bytes we may write after THRE: the TX FIFO depth if FIFOs are on */
static uint8_t rx[MAX_PAYLOAD];
static uint8_t sums_buf[MAX_SUMS * 4];

/* ---- CRC-32 (IEEE, reflected 0xEDB88320, as zlib) ---- */

static void crc_init(void)
{
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++)
            c = (c & 1) ? 0xedb88320u ^ (c >> 1) : c >> 1;
        crc_table[n] = c;
    }
}

static inline uint32_t crc_byte(uint32_t crc, uint8_t b)
{
    return crc_table[(crc ^ b) & 0xff] ^ (crc >> 8);
}

/* CRC of flash [off, off+len): word reads through XIP */
static uint32_t crc_flash(uint32_t off, uint32_t len)
{
    uint32_t crc = 0xffffffffu;
    const volatile uint32_t *w = (const volatile uint32_t *)(FLASH_BASE + off);
    for (uint32_t i = 0; i < len; i += 4) {
        uint32_t v = *w++;
        crc = crc_byte(crc, v);
        crc = crc_byte(crc, v >> 8);
        crc = crc_byte(crc, v >> 16);
        crc = crc_byte(crc, v >> 24);
    }
    return ~crc;
}

/* ---- UART ---- */

static int rx_ready(void) { return UREG(U_LSR) & LSR_DR; }

static void tx_wait_empty(void)
{
    while (!(UREG(U_LSR) & LSR_TEMT))
        ;
}

/* Sending: every byte goes through tx(), which keeps the running frame CRC */
static uint32_t tx_crc;
static int tx_room;
static uint16_t rom_div; /* the divisor the boot ROM left: 115200 baud */

static void tx(uint8_t b)
{
    if (tx_room == 0) {
        while (!(UREG(U_LSR) & LSR_THRE))
            ;
        tx_room = tx_burst;
    }
    UREG(U_THR) = b;
    tx_room--;
    tx_crc = crc_byte(tx_crc, b);
}

static void tx_begin(uint8_t type, uint32_t len)
{
    tx_room = 0;
    tx(0x5a);
    tx(0xa5);
    tx_crc = 0xffffffffu;
    tx(type);
    tx(len & 0xff);
    tx(len >> 8);
}

static void tx_u32(uint32_t v)
{
    tx(v);
    tx(v >> 8);
    tx(v >> 16);
    tx(v >> 24);
}

static void tx_end(void)
{
    uint32_t c = ~tx_crc;
    tx_u32(c);
}

static void send(uint8_t type, const uint8_t *p, uint32_t len)
{
    tx_begin(type, len);
    for (uint32_t i = 0; i < len; i++)
        tx(p[i]);
    tx_end();
}

static uint16_t get_divisor(void)
{
    uint32_t lcr = UREG(U_LCR);
    UREG(U_LCR) = lcr | LCR_DLAB;
    uint16_t d = (UREG(U_DLL) & 0xff) | ((UREG(U_DLH) & 0xff) << 8);
    UREG(U_LCR) = lcr & ~LCR_DLAB;
    return d;
}

static void set_divisor(uint16_t d)
{
    tx_wait_empty();
    for (int i = 0; i < 100000 && (UREG(U_USR) & 1); i++) /* DesignWare: LCR is locked while busy */
        ;
    uint32_t lcr = UREG(U_LCR) & ~LCR_DLAB;
    UREG(U_LCR) = lcr | LCR_DLAB;
    UREG(U_DLL) = d & 0xff;
    UREG(U_DLH) = d >> 8;
    UREG(U_LCR) = lcr;
}

static void uart_setup(void)
{
    /* The ROM sent its OK for RUN just before jumping here: let it leave before touching the FIFOs.
     * TEMT means the same with or without IER.PTIME, which the ROM leaves set. */
    tx_wait_empty();
    UREG(U_IER) = 0;    /* no interrupts, PTIME off: LSR.THRE now means "TX FIFO empty" */
    UREG(U_FCR) = 0x07; /* FIFOs on, both reset */
    /* IIR bits 7:6 read 11 when the FIFOs are enabled; then we may write 16 bytes per THRE */
    tx_burst = ((UREG(U_IIR) & 0xc0) == 0xc0) ? 16 : 1;
}

/* ---- receiving frames ---- */

/* Next frame into rx[]; returns its type, or -1 after `budget` empty polls (budget 0: wait forever).
 * A frame with a bad CRC or too long a payload is dropped and reported as -2. */
static int rx_byte(uint32_t *budget)
{
    for (;;) {
        if (rx_ready())
            return UREG(U_RBR) & 0xff;
        if (*budget) {
            if (--*budget == 0)
                return -1;
        }
    }
}

static int recv(uint32_t *len_out, uint32_t budget)
{
    uint32_t b0 = budget, *bp = &b0;
    int prev = -1, c;
    for (;;) {
        if ((c = rx_byte(bp)) < 0)
            return -1;
        if (prev == 0x5a && c == 0xa5)
            break;
        prev = c;
    }
    uint8_t h[3];
    for (int i = 0; i < 3; i++) {
        if ((c = rx_byte(bp)) < 0)
            return -1;
        h[i] = c;
    }
    uint32_t len = h[1] | (h[2] << 8);
    if (len > MAX_PAYLOAD)
        return -2;
    uint32_t crc = 0xffffffffu;
    for (int i = 0; i < 3; i++)
        crc = crc_byte(crc, h[i]);
    for (uint32_t i = 0; i < len; i++) {
        if ((c = rx_byte(bp)) < 0)
            return -1;
        rx[i] = c;
        crc = crc_byte(crc, c);
    }
    uint32_t got = 0;
    for (int i = 0; i < 4; i++) {
        if ((c = rx_byte(bp)) < 0)
            return -1;
        got |= (uint32_t)c << (8 * i);
    }
    if (got != ~crc)
        return -2;
    *len_out = len;
    return h[0];
}

/* The same frame format, one byte at a time and never waiting: for the BAUD wait, whose timing must not
 * stall on garbage (a host at the wrong rate sends plenty). -1 until a whole frame is in rx[]; then its
 * type, or -2 for a bad CRC. */
static struct { uint32_t n, len, crc, got; int prev; uint8_t h[3]; } fp;

static void feed_reset(void) { fp.n = 0; fp.prev = -1; }

static int feed(int c, uint32_t *len_out)
{
    if (fp.n == 0) {
        if (fp.prev == 0x5a && c == 0xa5) {
            fp.n = 1;
            fp.crc = 0xffffffffu;
            fp.got = 0;
        }
        fp.prev = c;
        return -1;
    }
    uint32_t i = fp.n++ - 1;
    if (i < 3) {
        fp.h[i] = c;
        fp.crc = crc_byte(fp.crc, c);
        if (i == 2) {
            fp.len = fp.h[1] | (fp.h[2] << 8);
            if (fp.len > MAX_PAYLOAD)
                feed_reset();
        }
        return -1;
    }
    i -= 3;
    if (i < fp.len) {
        rx[i] = c;
        fp.crc = crc_byte(fp.crc, c);
        return -1;
    }
    i -= fp.len;
    fp.got |= (uint32_t)c << (8 * i);
    if (i < 3)
        return -1;
    feed_reset();
    if (fp.got != ~fp.crc)
        return -2;
    *len_out = fp.len;
    return fp.h[0];
}

static uint32_t rd32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint16_t rd16(const uint8_t *p) { return p[0] | (p[1] << 8); }

static int range_ok(uint32_t addr, uint32_t len)
{
    return addr < FLASH_SIZE && len <= FLASH_SIZE - addr && !(addr & 3) && !(len & 3);
}

static void err(uint8_t code) { send(T_ERR, &code, 1); }

/* ---- commands ---- */

/* Stream [addr, addr+len) as DATA frames of up to 4 KB. A byte from the host stops the stream. */
static void do_read(uint32_t addr, uint32_t len)
{
    while (len) {
        if (rx_ready())
            return;
        uint32_t n = len < CHUNK ? len : CHUNK;
        tx_begin(T_DATA, 4 + n);
        tx_u32(addr);
        const volatile uint32_t *w = (const volatile uint32_t *)(FLASH_BASE + addr);
        for (uint32_t i = 0; i < n; i += 4)
            tx_u32(*w++);
        tx_end();
        addr += n;
        len -= n;
    }
}

static void do_sums(uint32_t addr, uint32_t len, uint32_t step)
{
    if (!range_ok(addr, len) || step == 0 || (step & 3) || (len + step - 1) / step > MAX_SUMS) {
        err(E_RANGE);
        return;
    }
    uint32_t n = 0;
    for (uint32_t a = addr; a < addr + len; a += step) {
        uint32_t k = addr + len - a < step ? addr + len - a : step;
        uint32_t c = crc_flash(a, k);
        sums_buf[n++] = c;
        sums_buf[n++] = c >> 8;
        sums_buf[n++] = c >> 16;
        sums_buf[n++] = c >> 24;
    }
    send(T_SUMSR, sums_buf, n);
}

/* Switch to divisor d, then wait `ms` for a PING at the new rate. The wait is timed by sending idle
 * 0xFF bytes at the new rate (10 bit times each), so it does not depend on the CPU clock. No PING:
 * go back to the old divisor. */
static void do_baud(uint16_t d, uint16_t ms)
{
    uint16_t old = get_divisor();
    send(T_OK, 0, 0);
    if (d == 0) {
        err(E_RANGE);
        return;
    }
    set_divisor(d);
    /* bytes per ms at the new rate = rate / 10 / 1000, rate = 115200 * rom_div / d (the ROM set
     * rom_div for 115200), so ms * 11.52 * rom_div / d */
    uint32_t fill = (uint32_t)ms * 1152u / 100u * rom_div / d;
    feed_reset();
    for (uint32_t i = 0; i < fill; i++) {
        /* at most one byte in per fill byte out, so the wait lasts `ms` whatever arrives */
        if (rx_ready()) {
            uint32_t len;
            if (feed(UREG(U_RBR) & 0xff, &len) == T_PING) {
                send(T_PONG, rx, len);
                return;
            }
        }
        tx_room = 0;
        while (!(UREG(U_LSR) & LSR_THRE))
            ;
        UREG(U_THR) = 0xff;
    }
    set_divisor(old);
}

void reader_main(void)
{
    uart_setup();
    crc_init();
    uint16_t d = rom_div = get_divisor();
    uint8_t hello[8] = { 'K', 'R', 'D', VERSION, d & 0xff, d >> 8, 0, 0 };
    send(T_HELLO, hello, sizeof hello);
    for (;;) {
        uint32_t len = 0;
        int t = recv(&len, 0);
        if (t == -2) {
            err(E_FRAME);
            continue;
        }
        switch (t) {
        case T_PING:
            send(T_PONG, rx, len);
            break;
        case T_BAUD:
            if (len >= 4)
                do_baud(rd16(rx), rd16(rx + 2));
            else
                err(E_FRAME);
            break;
        case T_READ:
            if (len >= 8 && range_ok(rd32(rx), rd32(rx + 4)))
                do_read(rd32(rx), rd32(rx + 4));
            else
                err(E_RANGE);
            break;
        case T_SUMS:
            if (len >= 12)
                do_sums(rd32(rx), rd32(rx + 4), rd32(rx + 8));
            else
                err(E_FRAME);
            break;
        default:
            err(E_TYPE);
        }
    }
}
