#pragma GCC optimize ("Os")
/*
 * krk_aprs_trec.c - F8a, the analog APRS beacon text's record (GitHub issue #3): 48 bytes in stock block 4 at 0xF90-0xFBF,
 * read and written through stock's flash_block_read / flash_block_write as the callsign record is (docs/settings-field.md 6,
 * docs/aprs-submenu.md 7.6). Blob ui3, beside the editor RAM it saves from (krk_tapedit.c); the editor screen and the beacon
 * (blob ui2) call it.
 *
 * Read on every use (no RAM copy). A vendor CPS write (FF) or a factory reset (00) fails the magic and checksum: no text, and the
 * beacon sends the default KRK_APRS_COMMENT, as before. Written only on the editor's OK and only when the text changed (one
 * sector rewrite, as any stock setting).
 */
#include "krk_tapedit.h"
#include "krk_config.h"
#include "krk_stock.h"

#define TXT_MAGIC 0x31545842u           /* "BXT1" */
#define TXT_BLOCK 4
#define TXT_OFF   0xF90
typedef char txt_rec_is_48_bytes[sizeof(struct krk_txt_rec) == 48 ? 1 : -1];
#define E krk_edit_ram.text

static u8 csum(const struct krk_txt_rec *r)
{
    const u8 *p = (const u8 *)r;
    u8 x = 0x69;
    unsigned i;

    for (i = 0; i < sizeof *r - 1; i++)
        x = (u8)((x << 1 | x >> 7) ^ p[i]);
    return x;
}

/* Only krk_aprs_text_store writes the record: zero after the text, characters the keypad types. A record that is not ours
 * reads as an empty text (the default), so "not set" and "empty" are one case everywhere. */
int krk_aprs_text_load(struct krk_txt_rec *r)
{
    int i;

    flash_block_read(flash_block_id_resolve_locked(TXT_BLOCK), TXT_OFF, r, sizeof *r);
    if (r->magic != TXT_MAGIC || r->csum != csum(r))
        for (i = 0; i < KRK_TXT_MAX; i++)
            r->text[i] = 0;
    r->csum = 0;
    for (i = 0; r->text[i]; i++)
        ;
    return i;
}

/* The record r holds the editor's text (both are zero after their end, so the whole arrays compare). */
static int same(const struct krk_txt_rec *r)
{
    int i = 0;

    while (i < KRK_TXT_MAX && r->text[i] == E.text[i])
        i++;
    return i == KRK_TXT_MAX;
}

int krk_aprs_text_store(void)
{
    struct krk_txt_rec r;
    struct krk_tap t = E.t;

    krk_aprs_text_load(&r);
    if (same(&r))
        return 1;                       /* unchanged: no flash write */
    krk_edit_ram.rec.magic = TXT_MAGIC; /* the editor RAM becomes the record for the write (text in place), then the editor again */
    krk_edit_ram.rec.csum = csum(&krk_edit_ram.rec);
    flash_block_write(TXT_BLOCK, TXT_OFF, &krk_edit_ram.rec, sizeof r);
    E.t = t;
    E.text[KRK_TXT_MAX] = 0;
    krk_aprs_text_load(&r);             /* verify through the same read path */
    return same(&r);
}

/* Called by the beacon in the UI or the maintenance task (48 bytes of stack). */
int krk_aprs_text_cat(char *dst, unsigned size)
{
    struct krk_txt_rec r;
    int n = krk_aprs_text_load(&r);

    krk_catn(dst, size, n ? r.text : KRK_APRS_COMMENT);
    return n > 0;
}
