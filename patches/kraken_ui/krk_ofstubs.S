/*
 * krk_ofstubs.S - glue for the Other Func nest (krk_vox.c "menus", docs/otherfunc-submenu.md).
 * Blob 2. Every site below sits inside a stock function whose prologue saved r15, so a bsr there
 * may clobber it; the exits are too far for br from patch flash, hence lrw + jmp.
 */
    .text

    .equ L5_INIT_EXIT,   0x03045082     /* 0x030442ac: pop r4, r15 */
    .equ L6_INIT_EXIT,   0x03046336     /* 0x03045fd8: addi sp,4; pop r4-r6, r15 */
    .equ L6_OK_EXIT,     0x03051686     /* 0x03050de8: addi sp,112; pop r4-r11, r15 */
    .equ L6_TITLE_BLANK, 0x03038eb0     /* 0x03038b9e: r3 = the empty string, then the draw tail */
    .equ L6_TITLE_DRAW,  0x03038ece     /* the draw tail: r3 = string (it loads r0-r2 itself) */

/* Call the C handler, then leave through a stock tail too far for br (r3 is free at every site). */
    .macro CALL_TAIL handler, tail
    bsr     \handler
    lrw     r3, \tail
    jmp     r3
    .endm

/* Level-6 hook over a stock `cmpnei r3,3; bt <exit>`: call handler(r0 = item); 0 = not ours, take
 * stock's exit (lrw + jmp, too far for br), non-zero = ours, return to the VOX arm. r15 is spilled
 * across the call; r0-r2 are dead at the sites. */
    .macro L6_HOOK_STUB handler, exit
    subi    sp, sp, 4
    st.w    r15, (sp, 0)
    mov     r0, r3
    bsr     \handler
    ld.w    r15, (sp, 0)
    addi    sp, sp, 4
    bnez    r0, 1f
    lrw     r3, \exit
    jmp     r3
1:  rts
    .endm

/* Entry 11 of the level-5 init table 0x030b0990, reached by a jmp out of 0x030442ac: the CW ID child
 * list (krk_vox.c krk_of_cwid_kids). */
    .global krk_of_cwid_kids_stub
krk_of_cwid_kids_stub:
    CALL_TAIL krk_of_cwid_kids, L5_INIT_EXIT

/* Over `cmpnei r3,3; bt16 0x03046336` at 0x0304623c. r3 = the level-4 item, lr = 0x03046240
 * (stock's VOX arm, which reloads r3). r0-r2 are dead there. */
    .global krk_of_l6_enter_stub
krk_of_l6_enter_stub:
    L6_HOOK_STUB krk_of_l6_enter, L6_INIT_EXIT

/* Over `bt32 0x03051686` at 0x0305102e, after `cmpnei r3,3`. r3 = the level-4 item,
 * lr = 0x03051032 (stock's VOX arm, which reloads r3). */
    .global krk_of_l6_ok_stub
krk_of_l6_ok_stub:
    L6_HOOK_STUB krk_of_l6_ok, L6_OK_EXIT

/* Over `cmpnei r3,3; bt16 0x03038eb0` at 0x03038c32. r3 = the level-4 item, lr = 0x03038c36
 * (stock's VOX title lookup). */
    .global krk_of_l6_title_stub
krk_of_l6_title_stub:
    subi    sp, sp, 4
    st.w    r15, (sp, 0)
    mov     r0, r3
    bsr     krk_of_l6_title
    ld.w    r15, (sp, 0)
    addi    sp, sp, 4
    cmpnei  r0, 1
    bf      1f                          /* 1: stock's own lookup */
    bez     r0, 2f
    mov     r3, r0                      /* a string of ours */
    lrw     r2, L6_TITLE_DRAW
    jmp     r2
2:  lrw     r3, L6_TITLE_BLANK
    jmp     r3
1:  rts

/* ---- the Burst Voice nest under Channel Set (krk_bvcfg.c, docs/chanset-submenu.md 8) ------------------------------------- */
    .equ CS_L4_EXIT,     0x03047cf4     /* 0x03047324 (Channel Set level-4 init): addi sp,104; pop r4-r5, r15 */
    .equ CS_L5_EXIT,     0x03045478     /* 0x03045084 (Channel Set level-5 init): pop r4-r7, r15 */
    .equ CS_L5_OK_EXIT,  0x0305040e     /* 0x030500b0 (Channel Set level-5 OK): addi sp,8; pop r4, r15 */
    .equ CS_L5_BLANK,    0x03038e9c     /* 0x03038528 header: r3 = the empty string, then the draw tail */

/* Over a stock `<cmp> <reg>,<imm>; bt <exit>` that ends a Channel Set row test: redo the compare; C clear = stock's own row,
 * return into the code after the pair; C set = not stock's: call handler (which tests the row itself), leave through exit.
 * Every site's function saved r15 in its prologue and pops it in exit. */
    .macro ROW_HOOK cmp, reg, imm, handler, exit
    \cmp    \reg, \imm
    bf      1f
    CALL_TAIL \handler, \exit
1:  rts
    .endm

/* Entry of the Burst Voice row in the relocated Channel Set level-4 init table (0x03047324, r2 = the row): the child list. */
    .global krk_cs_bv_kids_stub
krk_cs_bv_kids_stub:
    CALL_TAIL krk_cs_bv_kids, CS_L4_EXIT

/* Over `cmphsi r2,13; bt16 0x03045478` at 0x03045094: r2 = level-3 row - 3, the index of the 13-entry level-5 init table
 * 0x030b09e4 (rows 3..15). lr = 0x03045098, the table jump, which reads r2. */
    .global krk_cs_l5_enter_stub
krk_cs_l5_enter_stub:
    ROW_HOOK cmphsi, r2, 13, krk_cs_l5_enter, CS_L5_EXIT

/* Over `cmpnei r3,15; bt16 0x0305040e` at 0x0305031a, the last test of the level-5 OK chain (r3 = level-3 row). lr = 0x0305031e,
 * stock's row-15 arm, which reloads r3. */
    .global krk_cs_l5_ok_stub
krk_cs_l5_ok_stub:
    ROW_HOOK cmpnei, r3, 15, krk_cs_l5_ok, CS_L5_OK_EXIT

/* Over `cmpnei r3,15; bt16 0x03038e9c` at 0x03038ab8, the last test of the level-5 header's Channel Set arm (r3 = level-3 row).
 * lr = 0x03038abc, stock's row-15 lookup. Ours: r3 = the string and the draw tail, or stock's blank. */
    .global krk_cs_l5_title_stub
krk_cs_l5_title_stub:
    cmpnei  r3, 15
    bf      1f
    bsr     krk_cs_l5_title
    mov     r3, r0
    lrw     r2, L6_TITLE_DRAW           /* the same draw tail 0x03038ece serves every level's header */
    bnez    r0, 2f
    lrw     r2, CS_L5_BLANK
2:  jmp     r2
1:  rts
