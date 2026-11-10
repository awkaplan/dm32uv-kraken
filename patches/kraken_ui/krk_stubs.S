/*
 * krk_stubs.S - the four pieces of glue Snake's wiring needs, for the KRAKEN category.
 * Each one is Snake's own stub with the handler renamed; see dm32uv-re tools/examples/snake/
 * (snake_menu_append_trampoline.S, snake_ok_stub.S, snake_tail_stub.S) for the full reasoning.
 * The epilogues are too far for br16/br32 from patch flash, hence lrw + jmp (r3 is free at
 * every one of these sites).
 */
#include "krk_menu_rows.h"          /* KRK_<HOST>_FIRST / _MAX: our item numbers per stock-menu host (generated) */

    .text

    .equ IDX23_EPILOGUE, 0x0304a112     /* menu_option_gen_idx23's shared tail */
    .equ STOCK_CASE_TAIL, 0x0303d872    /* render/item case blocks' shared tail */
    .equ STOCK_OK_EPILOGUE, 0x0303cde6  /* OK-path stubs' shared tail */

/* ---- shared stub shapes (tools/check_bsr.py expands these macros before checking the branches) ---- */

/* The commonest stub: call the C handler (optionally passing a row in r0), then leave through a stock
 * tail that is too far for br16/br32. r3 is free at every site that uses it. */
    .macro CALL_TAIL handler, tail, row=
    .ifnb \row
    mov     r0, \row                /* the level-3 row; the handler range-checks it itself */
    .endif
    bsr     \handler
    lrw     r3, \tail
    jmp     r3
    .endm

/* Item-action compare chain (Call Log, GNSS, Radio Settings): bsr leaves C as the chain's last compare
 * set it and r3 = the item. `special` is the stock block for the item the chain still owns (taken when C
 * says so: bf 1f); `first` (optional) re-does the compare that selects it. Then r3 is range-checked
 * against `bound` and `oob` (bt or bf, the sense differs by site) branches to stock's out-of-range exit,
 * else the handler runs with r0 = the item (items below ours never get here: the chain diverted them). The
 * enclosing dispatcher's prologue saved r15 and its tail pops it, so these stubs never return. */
    .macro ITEM_CHAIN_STUB handler, bound, oob, special, tail, first=
    .ifnb \first
    cmpnei  r3, \first
    .endif
    bf      1f
    cmphsi  r3, \bound
    \oob    2f
    mov     r0, r3
    bsr     \handler
2:  lrw     r3, \tail
    jmp     r3
1:  lrw     r3, \special
    jmp     r3
    .endm

/* Spill / reload registers around a C call, packed from sp+0 in the order listed. FRAME_LOAD reloads in
 * the same order; FRAME_LOAD_REV expects the list in reverse order (the last register spilled first). */
    .macro FRAME_SAVE size, regs:vararg
    subi    sp, sp, \size
    .set    __fo, 0
    .irp    r, \regs
    st.w    \r, (sp, __fo)
    .set    __fo, __fo + 4
    .endr
    .endm
    .macro FRAME_LOAD size, regs:vararg
    .set    __fo, 0
    .irp    r, \regs
    ld.w    \r, (sp, __fo)
    .set    __fo, __fo + 4
    .endr
    addi    sp, sp, \size
    .endm
    .macro FRAME_LOAD_REV size, regs:vararg
    .set    __fn, 0
    .irp    r, \regs
    .set    __fn, __fn + 4
    .endr
    .set    __fo, __fn
    .irp    r, \regs
    .set    __fo, __fo - 4
    ld.w    \r, (sp, __fo)
    .endr
    addi    sp, sp, \size
    .endm

/* Hooked over `st16.h r3,(r4,0); br16 0x304a112` at 0x03049dca: redo the count store, append top-level item index 10,
 * leave through the tail. At the hook r3 = the new count, r4 = cursor pointer, r5 = list buffer base
 * (snake_menu_append_trampoline.S). The splice covers the store as well as the br16 because the halfword after the br16
 * is padding only in some builds (firmware/l01/overrides.json, docs/firmware-builds.md). */
/* APRS (category id 9) is always shown, by request (2026-10-15): stock appends it only when its
 * settings gate is set (CPS APRS on) and never in the short idx24 list. Before appending KRAKEN,
 * scan the list just built (r5[0..count), count at (r4,0)) and append 9 if stock left it out.
 * Uses r1-r3 only (r0 untouched); leaves r3 = count.
 * Also forces category 4 (Call Log) the same way: stock hides it on analog channels, which made its
 * Last Heard row (item 4) unreachable there. Parameterised by category id. */
    .macro APPEND_CAT_IF_MISSING id
    ld.h    r3, (r4, 0)
    movi    r1, 0
91: cmphs   r1, r3
    bt      92f                     /* scanned all: not there */
    ldr.b   r2, (r5, r1 << 0)
    cmpnei  r2, \id
    bf      93f                     /* already listed */
    addi    r1, 1
    br      91b
92: movi    r2, \id
    str.b   r2, (r5, r3 << 0)
    addi    r3, 1
    zexth   r3, r3
    st.h    r3, (r4, 0)
93:
    .endm

/* The whole append: guarantee APRS (9) and Call Log (4), then add 10 and leave r3 = the new count.
 * `zext` (non-blank) zero-extends the count before the store, as idx24's caller wants. */
    .macro APPEND_TOP_ITEMS zext=
    APPEND_CAT_IF_MISSING 9
    APPEND_CAT_IF_MISSING 4
    ld.h    r3, (r4, 0)
    movi    r2, 10
    str.b   r2, (r5, r3 << 0)
    addi    r3, 1
    .ifnb \zext
    zexth   r3, r3
    .endif
    st.h    r3, (r4, 0)
    .endm

    .global krk_menu_append
krk_menu_append:
    st.h    r3, (r4, 0)             /* the displaced store: the count stock just incremented */
    APPEND_TOP_ITEMS
    lrw     r3, IDX23_EPILOGUE
    jmp     r3

/* Second top-level list builder, menu_option_gen_idx24 (the short list shown in VFO mode: Scan,
 * Settings, GNSS). Hooked over the 4 bytes `lrw r2,[0x3049fec]; ld.b r2,(r2,0)` at 0x03049e0a,
 * right after item 5's append, because its three exits are a bez32 pair and a final br16 with no
 * room for a bsr. Same registers as idx23 (r4 cursor ptr, r5 buffer); appends 10, leaves r3 =
 * the new count for the conditional item-8 append that follows, then re-executes the two displaced
 * instructions. r15 is dead here: idx24 is a case block of menu_option_list_dispatch, whose
 * prologue pushed r15 and whose shared tail 0x0304a112 pops it. */
    .global krk_menu_append24
krk_menu_append24:
    APPEND_TOP_ITEMS zext
    lrw     r2, 0x0003fdc0          /* the value at stock literal 0x03049fec */
    ld.b    r2, (r2, 0)
    rts

/* 11th entry of the OK-path table: open the sub-list. */
    .global krk_ok_stub
krk_ok_stub:
    CALL_TAIL krk_category_entry, STOCK_OK_EPILOGUE

/* 11th entry of the render-path table: a case block, not a function (Snake v11). */
    .global krk_render_stub
krk_render_stub:
    CALL_TAIL krk_category_entry, STOCK_CASE_TAIL

/* Rows appended to the stock APRS sub-list: entries 8.. of the relocated APRS per-item jump table
 * (stock 0x030aff54), dispatched from case block 0x0303d54c with the enclosing frame live. */
    .global krk_aprs_item_stub
krk_aprs_item_stub:
    CALL_TAIL krk_aprs_item, STOCK_CASE_TAIL

/* Call Log (category id 4) item-action case block 0x0303cf80 is a compare chain, not a table:
 *   item = level-2 list[row]; ==1 / ==0 / ==2 -> stock blocks; cmpnei r3,3; bt tail; br item-3 block
 * The 4-byte `bt 0x0303d872` at 0x0303cf98 is replaced by `bsr krk_calllog_item_stub`. bsr leaves C
 * as `cmpnei r3,3` set it (clear = item 3) and r3 = the item. The enclosing dispatcher's prologue saved
 * r15 and its shared tail pops it, so this stub never returns: it ends in the tail or the stock
 * item-3 (Delete) block, which itself ends in the tail. Our items are KRK_CALLLOG_FIRST.. (menu_hosts.json,
 * docs/calllog-submenu.md); anything past them takes stock's out-of-range exit, as before. */
    .equ CALLLOG_ITEM3, 0x0303cfb6
    .global krk_calllog_item_stub
krk_calllog_item_stub:
    /* clear C = item 3: stock Delete; past our items: stock's out-of-range exit (bt) */
    ITEM_CHAIN_STUB krk_calllog_item, KRK_CALLLOG_FIRST + KRK_CALLLOG_MAX, bt, CALLLOG_ITEM3, STOCK_CASE_TAIL

/* Radio Settings (Settings = category 5, level-2 item 0) enter chain 0x030431cc, a compare chain like
 * Call Log's: items 1 / 0 / 2 are diverted by bf16s, then `cmpnei r3,3` at 0x030431e4 and the 4 bytes
 * `bt16 0x030432ea; br16 0x030432ac` at 0x030431e6 become `bsr krk_rs_item_stub`. bsr leaves C as the
 * cmpnei set it (clear = item 3) and r3 = the item. The dispatcher is a real function whose prologue
 * pushed r15 and whose epilogue 0x030432ea pops it, so the bsr's clobber of r15 is harmless and this
 * stub must not return: it ends in that epilogue, or in stock's item-3 block (which ends there too).
 * Item 4 = our appended row (docs/settings-submenu.md). */
    .equ RS_ITEM3,   0x030432ac     /* stock "Other Func" */
    .equ RS_EPILOGUE, 0x030432ea    /* pop16 lr */
    .equ RS_ITEM_LO, KRK_RS_FIRST   /* our items are KRK_RS_FIRST .. + KRK_RS_MAX - 1 */
    .global krk_rs_item_stub
krk_rs_item_stub:
    /* clear C = item 3: stock Other Func; cmphsi sets C when item >= 4 (one of ours), so the
     * out-of-range exit for "below 4 (and not 3)" is `bf`, the opposite sense to Call Log's `bt`. The
     * handler range-checks the item it is passed itself. */
    ITEM_CHAIN_STUB krk_rs_item, RS_ITEM_LO, bf, RS_ITEM3, RS_EPILOGUE

/* OK *inside* the option level of a row appended to stock Radio Settings (docs/settings-submenu.md).
 *
 * MEASURED 2026-11-03: opening a row and committing a value are two different key events handled by
 * two different functions. The open OK runs 0x0303a770 (where the category-5 edge 0x0303ab38 lives);
 * the committing OK runs 0x0303a57c -> bsr 0x0304e560 -> jump table 0x030b11c4 -> entry 0 (Radio
 * Settings) = 0x0304e5a0. That entry is a pure no-op in stock (`movi r0,35; movi r1,1; bsr set_mode`,
 * 8 bytes) reached by nothing else in the image - the literal 0x0304e5a0 occurs only in its own
 * table slot - so taking it over cannot lose stock behaviour. Confirmed by breakpoint: it fires
 * exactly once, only on the committing OK.
 *
 * 0x0304e560's prologue is `push r15` ONLY (r4 is not saved), unlike the 0x0303a770 frame that
 * opt_ok_hook sits in, so this stub saves r4 itself before calling C. Its epilogue is 0x0304e83a
 * (`pop r15`); the stub never returns, it jumps there. krk_settings_opt_ok() identifies the row and
 * either commits it or performs the stock set_mode(0x23, 1) we displaced. */
    .equ RS_OK_EPILOGUE, 0x0304e83a
    .global krk_settings_ok_stub
krk_settings_ok_stub:
    FRAME_SAVE 8, r4, r15
    bsr     krk_settings_opt_ok
    FRAME_LOAD 8, r4, r15
    lrw     r3, RS_OK_EPILOGUE
    jmp     r3

/* 11th entry of the item-action table: run the selected screen. */
    .global krk_item_stub
krk_item_stub:
    CALL_TAIL krk_item_ok, STOCK_CASE_TAIL

/* Stock APRS distance/bearing hemisphere fix (docs/aprs-stock-gaps.md B.6).
 * gps_position_display_screen @0x030636e0 feeds four unsigned decimal-degree doubles (our lat/lon,
 * the received station's lat/lon) to its bearing and haversine routines without ever applying the
 * N/S/E/W letters, so for a user west of Greenwich the bearing is mirrored E/W. Those routines reject
 * values <= 0, so W/S values become 360 - x (exact for sin/cos/haversine; keeps the numeric quadrant
 * tests right for same-hemisphere pairs). Hooked over `ld.w r2,(sp,0x18); st.w r2,(sp,0)` at
 * 0x03063a04 (after normalize_pair, before the calc/distance argument setup); r15 is saved by the
 * caller's prologue. Caller-frame slots: our lat 0x34/0x38, our lon 0x2c/0x30, station lon
 * 0x14/0x20 (non-contiguous halves), station lat 0x18/0x1c; station lon letter at sp+0x24. Our letters
 * at 0x38d42/0x38d43 (GPS parser); the station lat letter is re-read from the record text 0x3e923
 * after its 3rd comma. Clobbers r0-r3 only, as the caller allows; re-executes the displaced pair. */
    /* Stock calls go by NAME (resolved by dm32_stock_symbols.ld into a real PC-relative bsr).
     * NEVER `bsr <.equ absolute>`: this C-SKY gas emits NOTHING for it, silently (found
     * 2026-10-15: the first version of this stub had no calls at all). tools/check_bsr.py
     * fails the build if any bsr/br here (also inside macro bodies, after argument substitution) targets an
     * absolute value or an .equ symbol. It does not check assembled sizes. */
    .equ F, 20                      /* push r4-r7, r15 */
    .macro FLIP lo, hi
    movi    r0, 0
    movih   r1, 0x4076
    ori     r1, r1, 0x8000          /* r1:r0 = 360.0 */
    ld.w    r2, (sp, \lo + F)
    ld.w    r3, (sp, \hi + F)
    bsr     soft_double_sub          /* r1:r0 = r1:r0 - r3:r2 */
    st.w    r0, (sp, \lo + F)
    st.w    r1, (sp, \hi + F)
    .endm

    .global krk_hook_bearing
krk_hook_bearing:
    push    r4-r7, r15
    movih   r4, 0x3
    ori     r4, r4, 0x8d42          /* our lat letter; +1 = our lon letter */
    ld.b    r3, (r4, 0)
    cmpnei  r3, 0x53                /* 'S' */
    bt      1f
    FLIP    0x34, 0x38
1:  ld.b    r3, (r4, 1)
    cmpnei  r3, 0x57                /* 'W' */
    bt      2f
    FLIP    0x2c, 0x30
2:  ld.b    r3, (sp, 0x24 + F)
    cmpnei  r3, 0x57
    bt      3f
    FLIP    0x14, 0x20
3:  movih   r4, 0x3
    ori     r4, r4, 0xe923          /* station record text */
    mov     r0, r4
    movi    r1, 3
    bsr     string_find_nth_comma_index
    zextb   r0, r0
    ldr.b   r3, (r4, r0 << 0)
    cmpnei  r3, 0x53
    bt      4f
    FLIP    0x18, 0x1c
4:  ld.w    r2, (sp, 0x18 + F)      /* displaced stock instructions */
    st.w    r2, (sp, 0 + F)
    pop     r4-r7, r15

/* Analog APRS (v39): the stock "APRS active on this channel" bit, [0x40078] bit 0, is computed in
 * channel_change_apply_dispatcher at 0x03057a98.. from the channel's report mode (byte 0x1C bits 3-2
 * == 1, "APRS On") AND the mode nibble (r3 = flags8 & 0xf0) being 0x10 or 0x30 (DMR). Stock:
 *   cmpnei r3,0x10 ; bf set ; cmpnei r3,0x30 ; bt clear ; set: ...
 * Hooked over the 4-byte `cmpnei r3,0x30` at 0x03057aa8: re-executes it, and when r3 is not 0x30
 * tests for 0 (analog FM) instead, so the following stock `bt clear` keeps the bit for analog
 * channels too. Returns with C set = clear the bit. The caller's r15 is on its stack; r3 is the live
 * nibble and is left untouched. With the bit set, the stock periodic gate (0x03054880) and the
 * manual Upload Beacon key (0x0301587e) both run on analog channels; the sends themselves are
 * redirected to krk_aprs_tx.c (manifest splices aprs_tick_hook / aprs_send_now_hook). */
/* Review fix 6 (F12): the same site is the channel-change hook for the extension blob (krk_ext_hooks[2] =
 * channel change: the F12 scrambler re-applies SCRAMBLE_REG right when the new channel's mode/cipher flags are
 * loaded). A C function is called from here, so every register the C ABI may clobber (r0-r3, r12, r13) and the
 * link register r15 are saved around it; r4-r11 are callee-saved. C is recomputed by the cmpnei afterwards. */
    .global krk_hook_aprs_mode
krk_hook_aprs_mode:
    FRAME_SAVE 32, r0, r1, r2, r3, r12, r13, r15
    bsr     krk_hook_chan_change    /* krk_lastheard.c: krk_ext_hook(2) */
    FRAME_LOAD_REV 32, r15, r13, r12, r3, r2, r1, r0
    cmpnei  r3, 0x30
    bf      1f                      /* DMR 0x30: C clear, stock sets the bit */
    cmpnei  r3, 0                   /* analog: C clear; anything else (0x20): C set */
1:  rts

/* Channel-commit epilogue hook (docs/dual-watch.md). commit_channel_record_dual_vfo @0x030303bc ends
 * `ld.w r15,(sp,0); addi sp,sp,0x14; rts` at 0x03030550 and every path of it (all the live-struct writes in
 * the firmware) branches there. The 4-byte load is replaced by `bsr krk_commit_end_stub`, so the stub arrives
 * with r15 = 0x03030554 and the real return address still at (sp,0). It parks the stub's return, runs the C
 * hook (clobbers r0-r3, r12, r13: all dead at the epilogue; r4+ are callee-saved) and then re-executes the
 * displaced load before continuing at 0x03030554. */
    .global krk_commit_end_stub
krk_commit_end_stub:
    subi    sp, sp, 8
    st.w    r15, (sp, 0)
    bsr     krk_hook_chan_commit
    ld.w    r2, (sp, 0)
    addi    sp, sp, 8
    ld.w    r15, (sp, 0)
    jmp     r2

/* Title of the stock level-2 screen for category ids >= 10 (docs/stock-option-level.md 2.4). Reached by `bsr` from 0x030385f4 inside
 * menu_field_value_display_dispatch (0x03038528), whose frame (push r4,r15; 24 bytes) is live: stock there does `lrw r3,<empty string>;
 * br 0x03038ece`, and 0x03038ece draws r3 as the centered white header (display_draw_number_centered(1, 0xffff, 0, r3)) and
 * returns through the frame's epilogue. So: r3 = krk_opt_title(), jump to 0x03038ece; never return (r15 is dead). */
    .global krk_title_stub
krk_title_stub:
    bsr     krk_opt_title
    mov     r3, r0
    lrw     r2, 0x03038ece
    jmp     r2

/* OK at an option level opened from the stock APRS list (category 9). The stock OK handler 0x0304d6c0 dispatches the level-2 item
 * (0x3e662 list) through a compare chain and, for the last test `cmpnei r3,7; bt 0x0304d846` at 0x0304d81c, treats every item
 * that is not 7 as "nothing to do". The 4 bytes are replaced by `bsr krk_aprs_ok_stub`: item 7 returns to the stock block that
 * follows (0x0304d820), items >= 8 (appended rows) go to krk_aprs_opt_ok and then to the handler's epilogue 0x0304d846
 * (`addi sp,8; pop r15`: r15 was saved by the prologue, so the bsr's clobber is harmless). r3 = the item. */
    .global krk_aprs_ok_stub
krk_aprs_ok_stub:
    cmpnei  r3, KRK_APRS_FIRST - 1  /* 7, the last stock item */
    bf      1f
    mov     r0, r3
    bsr     krk_aprs_opt_ok
    lrw     r3, 0x0304d846
    jmp     r3
1:  rts

/* Header of the stock text-entry screen (subtype 8, handler 0x0303b906 -> 0x0303bb1e): with [0x3e31e] bit 6 set stock executes
 *     0x0303bb30  lrw r3,[0x030ba280]          (2 bytes)
 *     0x0303bb32  ldr.w r3,(r3,r12<<2)         (4 bytes)   r12 = language bit
 *     0x0303bb36  bsr 0x03018b5c               r0 = 1, r1 = 0xffff, r2 = 0 were loaded just before (0x0303bb28..2e)
 * The 6 bytes become `bsr krk_numtitle_stub; nop16`. The stub keeps r0-r2 (C clobbers them) and returns r3 = krk_num_title(). r12 is not used after. */
    .global krk_numtitle_stub
krk_numtitle_stub:
    FRAME_SAVE 16, r0, r1, r2, r15
    bsr     krk_num_title
    mov     r3, r0
    FRAME_LOAD 16, r0, r1, r2, r15
    rts

/* GNSS (category id 8) item-action case block 0x0303d2a0 is a compare chain: item 0 / 1 / 2 / 3 are
 * diverted above, then at 0x0303d4a8 `cmpnei r3,4; bt16 0x0303d872` (4 bytes) guards the item-4
 * block that falls through at 0x0303d4ac. Those 4 bytes become `bsr krk_gnss_item_stub`, with r3 =
 * the item (4 or more here). The enclosing dispatcher's prologue saved r15; its shared tail pops it,
 * so this stub never returns. Our items are KRK_GNSS_FIRST (5).. (menu_hosts.json, docs/gnss-submenu.md). */
    .equ GNSS_ITEM4, 0x0303d4ac
    .global krk_gnss_item_stub
krk_gnss_item_stub:
    /* item 4 (KRK_GNSS_FIRST - 1): stock GNSS Mode block; past our items: stock's out-of-range exit (bt) */
    ITEM_CHAIN_STUB krk_gnss_item, KRK_GNSS_FIRST + KRK_GNSS_MAX, bt, GNSS_ITEM4, STOCK_CASE_TAIL, KRK_GNSS_FIRST - 1

/* Rows appended to the stock Channel Set sub-list (Settings = category 5, level-2 item 1).
 * docs/chanset-submenu.md. Unlike Radio Settings, BOTH key events are jump tables indexed by the
 * level-3 row, so the row arrives in a register at each site and no latch is needed.
 *
 * ENTER - table_entry_select_and_index_list_build_toplevel_dispatch @0x03047324:
 *     push r4-r5,r15 ; subi sp,104 ; r2 = level-3 row ; cmphsi r2,22 ; bt 0x03047cf4
 *     lrw r3,0x030b0a18 ; ldr.w r3,(r3,r2<<2) ; jmp r3
 * so at a table entry r2 = the row and r3 is dead. These are CASE BLOCKS of that function: the
 * prologue has run and the epilogue 0x03047cf4 (`addi sp,104; pop r4-r5,r15`) ends them, so the
 * bsr's clobber of r15 is harmless and the stub must not return. The relocated table's entries
 * 22..23 point here (manifest krk_cs_itemjmp). */
    .equ CS_ENTER_EPILOGUE, 0x03047cf4
    .global krk_cs_item_stub
krk_cs_item_stub:
    CALL_TAIL krk_cs_item, CS_ENTER_EPILOGUE, r2        /* row = r2 here (r3 at COMMIT) */

/* COMMIT - 0x0304dc8c, reached by the committing OK (event 0x22) through
 *     0x0303a57c -> bsr 0x0304e560 -> jump table 0x030b11c4[1] = 0x0304e5aa -> bsr 0x0304dc8c
 * whose body is
 *     push r4-r8,r15 ; subi sp,48 ; r3 = level-3 row ; cmphsi r3,22 ; bt 0x0304e416
 *     lrw r2,0x030b116c ; ldr.w r2,(r2,r3<<2) ; jmp r2
 * so at a table entry r3 = the row and r2 is dead. Also case blocks: the prologue has run and the
 * epilogue 0x0304e55a (`addi sp,48; pop r4-r8,r15`) ends them. 0x0304e416 is NOT an epilogue (it is
 * the stock Channel-Name/numeric out-of-range path), so a row of ours must never fall out through
 * the bound - hence the bound widening in the manifest. The relocated table's entries 22..23 point
 * here (manifest krk_cs_okjmp). */
    .equ CS_OK_EPILOGUE, 0x0304e55a
    .global krk_cs_ok_stub
krk_cs_ok_stub:
    CALL_TAIL krk_cs_opt_ok, CS_OK_EPILOGUE, r3
