/*
 * krk_tapedit.h - keypad text entry shared by the callsign editor (krk_callsign.c) and the APRS beacon text editor
 * (krk_aprs_text.c, F8a), and the beacon text's record. The code is krk_tapedit.c and krk_aprs_trec.c in blob ui3; blob ui2
 * binds krk_tap_key, krk_edit_ram and the krk_aprs_text_* functions through the manifest defsym.
 */
#ifndef KRK_TAPEDIT_H
#define KRK_TAPEDIT_H

#include "krk.h"

/* An editor's typing state. The text it edits is the caller's buffer: zero from len on, with room for max + 1 bytes. */
struct krk_tap {
    u8 len;
    signed char key;                /* digit key of the pending multi-tap, -1 none */
    u8 ticks;                       /* keypad ticks since that key: 10 (1 s) accepts the character */
    u8 lower;                       /* letters come out in lower case */
};

/* What keys 0 and 1 type (2-9 type their letters, then the digit) and the longest text. */
struct krk_tap_spec {
    const char *k0, *k1;            /* first press first; distinct characters, ending in the key's digit */
    u8 max;
};

/* One keypad tick (krk_screen.tick's scan). Digits multi-tap (the same key again within 1 s: the next character of its set, in
 * place), Up/Down step the last character through A-Z 0-9 (an empty text, or a last character outside that order, starts at
 * 'A'), '*' deletes the last character; SCAN_NONE counts the multi-tap timeout. Returns 1 when the text changed (draw it), 0
 * for SCAN_NONE, -1 for any other key, which it leaves alone (a pending multi-tap stays): the caller's to handle. */
int krk_tap_key(struct krk_tap *t, char *buf, const struct krk_tap_spec *s, u8 scan);

/* The beacon text's record (F8a): 48 bytes in stock block 4 at 0xF90 (docs/settings-field.md 6). */
#define KRK_TXT_MAX 43
struct krk_txt_rec {
    u32 magic;                      /* "BXT1" */
    char text[KRK_TXT_MAX];         /* zero-padded; all 43 used = no terminator */
    u8 csum;                        /* rotate-xor of the 47 bytes before it, seed 0x69 */
};
int krk_aprs_text_load(struct krk_txt_rec *r);  /* text length; a record not of ours (00 / FF / damaged) reads as empty. csum
                                                 * comes back 0: it terminates text[] */
int krk_aprs_text_store(void);                  /* OK in the editor: krk_edit_ram.text saved if changed; 1 = the record holds it */
/* krk_aprs_text_cat (the beacon comment) is declared in krk.h for krk_aprs_tx.c */

/* RAM of the one editor screen that is open: the callsign and the beacon text editors (the runner runs one screen at a time,
 * and each editor sets its state up in its enter()). In blob ui3's .bss. */
union krk_edit_ram {
    struct {                        /* krk_callsign.c */
        struct krk_tap t;
        char call[7];               /* CALL_MAX + 1 */
        u8 ssid;
        u8 field;                   /* 0 callsign, 1 SSID */
        u8 dirty;
        char status[20];
    } call;
    struct {                        /* krk_aprs_text.c */
        struct krk_tap t;
        char text[KRK_TXT_MAX + 1]; /* zero from t.len on, [43] stays 0 */
    } text;
    struct krk_txt_rec rec;         /* the same bytes as a record: text at the same offset (load and store go through it) */
};
extern union krk_edit_ram krk_edit_ram;

#endif
