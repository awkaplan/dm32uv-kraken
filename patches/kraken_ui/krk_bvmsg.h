/*
 * krk_bvmsg.h - F13 message packing between the unit store (27-byte AMBE+2 units) and the BV1 burst payload (27-byte chunks). Pure
 * integer code shared by the Burst Voice screen (krk_bvscr.c) and the host test (tests/test_f13.py). docs/burst-voice.md section 11.
 */
#ifndef KRK_BVMSG_H
#define KRK_BVMSG_H

#include "krk_bv.h"

#define BVM_NU_MAX      92          /* units in a message (5.52 s) */
#define BVM_B_CAP       2106        /* payload buffer: 78 chunks = 13 blocks */
#define BVM_MAX_CHUNKS  78

/* Packs n units for the air. Detects the AMBE layout: a stripped + silence-trimmed payload (flags TRIMMED|STRIPPED|layout|parity),
 * or the raw units when no layout is found. out holds BVM_B_CAP bytes. Returns the chunk count (> 0), -1 for no message, -2 when a raw
 * message does not fit BVM_MAX_CHUNKS. *layout = the krk_ambe layout used (0 = raw). */
int krk_bvm_pack(const unsigned char *units, int n, unsigned char *out, int *flags, int *layout);
/* Rebuilds the units from a received burst (info after BV_EV_DONE, chunks in b as they landed). Lost blocks of a raw message become
 * silence units; a stripped payload with a lost block usually fails to unpack. Returns the unit count or <= 0. */
int krk_bvm_unpack(const krk_bv_info_t *in, unsigned char *b, unsigned char *a, int cap_units);
/* Estimate of the chunks a message needs (no trim): layout from the units, 147 bits per unit when stripped. */
int krk_bvm_chunks_estimate(const unsigned char *units, int n, int *layout);

/* Fills n units with the deterministic non-silent AMBE+2 test message (see krk_bvmsg.c). */
void krk_bvm_testmsg(unsigned char *units, int n);

#endif
