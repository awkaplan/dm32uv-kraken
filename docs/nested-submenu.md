# Nesting a sub-menu under a stock Channel Set row

> **2026-11-06 correction.** Section 3 below could not name what overwrote the option level's cells.
> It is the menu engine itself: a level's cells are filled by that level's RENDER entry, which runs
> after every OK handler, so cells installed from an OK handler never survive. With that model a
> working nest was built in the Other Func tree, where stock nests natively:
> `docs/otherfunc-submenu.md`, and then **under Channel Set itself** (section 7, Burst Voice). The
> measurements below stand; the explanation in section 3 does not.

Investigated 2026-11-03. The goal was to turn two features into **nested** sub-menus of the stock
Channel Set list, so that selecting a parent row opens a further list of child rows:

```
Scrambler  ->  { Scrambler On/Off, Key }
Parrot     ->  { Parrot On/Off, Replay Audio, Check, Clear Audio, Status }
```

**Outcome: a child list one level deeper works and is reproducible. The level below it - a child's own
option level - does not, and the blocker is in a stock code path with no private splice site.** The
nine rows therefore ship FLAT under Channel Set, with the user-facing labels the nested layout asked
for. Everything below is measured; nothing is inferred from analogy.

Every address is from the pristine stock image
`progmem_stock_DP570UV_20260926_FULL.bin`; the live findings come from QEMU runs of this build. The
probe scripts were one-off QEMU runs and are not in the repo: `nest_probe.py` (the state at each level of the stock nest), `nest_probe2.py` / `nest_probe3.py` /
`nest_probe5.py` (per-keypress hit attribution on the stock nest), `nest_probe6.py` (the same on our own
rows). The three throwaway cell-dump scripts the section-3 evidence came from were not kept; the
numbers they produced are quoted verbatim below and `nest_probe6.py` is the one that matters.

## 1. Stock already has a five-level nest

The decisive early finding, and the reason this looked cheap. `Settings > Radio Settings > Voice Func
> Battery Low > Off/On` is five levels deep, and `nest_probe.py` walked it and dumped the state
at each one:

| level | keys | history depth | mode code | cells |
|---|---|---|---|---|
| L2 Settings | `lead menu down ok` | 2 | `0x0e` | 7 rows, subtype 2, icon 1 |
| L3 Radio Settings' rows | `... ok` | 3 | `0x15` | 6 rows (4 stock + our 2), icon 1 |
| L4 Voice Func's children | `... ok` | 4 | `0x1c` | 11 rows, icon 1 |
| L5 Battery Low's values | `... ok` | 5 | `0x23` | 2 rows (OFF/ON), **icon 2** |

Three pieces of generic machinery make that work, and all three are level-agnostic:

**The history push `0x0303699c`** runs on every OK at every depth, *before* the item-action. Decoded
in full:

```
H = 0x320ac                                  {d0, d1, d2, mode}
0x3e908[H[1]] = [0x3e5d5] ; H[1]++           the highlight slot
0x3e4e8[H[0]] = [0x3e5cc] ; H[0]++           THE CURSOR -> this is the "row chosen at depth d" byte
0x3e5e0[H[2]] = H[3]      ; H[2]++           the mode code
S = 0x3e5f8 ; n = S[0x2e2]
memcpy(S + 38 + 35*n, S + 2, 35) ; S[0x2e2] = n + 1     the whole 35-byte active index list
[0x18000b07] = -16
```

So the saved index lists land at `0x3e5f8 + 38 + 35*n` = `0x3e61e`, `0x3e641`, `0x3e664`, `0x3e687`,
`0x3e6aa`, which are exactly the arrays the firmware reads as `0x3e63f+2`, `0x3e662+2`, `0x3e685+2`,
`0x3e6a8+2`. `history_queue_pop 0x03036c00` undoes it. **Nothing about depth has to be hooked.**

**The mode code advances by 7 per level** (`0x07` L1, `0x0e` L2, `0x15` L3, `0x1c` L4, `0x23` L5,
`0x2a`, `0x31`, `0x38`) and the header dispatcher `0x03038528` indexes its 50-entry table
`0x030afcdc` by `mode - 7`. Dumping that table shows a **real handler for every one of the eight
level codes** (`0x0303858c`, `0x030385a2`, `0x030385c4`, `0x03038720`, `0x03038994`, `0x03038b9e`,
`0x03038d24`, `0x03038de2`); everything else is the `0x03038e20` default. The push does **not**
advance the mode code - stock's own deep arms do it themselves with `set_mode(code, 1)`
(`0x03035ec4`: `[0x320ac+3] = code`, and `[0x320ac+4] = flag` when flag is non-zero).

**The renderer is the same at every depth.** Subtype 2 with icon mode 1 is a numbered sub-list and
icon mode 2 is a radio-mark option level, at L3 as at L5; Up/Down, the scroll bar and the OK/BACK bar
are one code path.

## 2. Which handler each keypress reaches

Settled by `hits_before_last_key` vs `hits_FROM_last_key`, per keypress, with the resulting depth read
after each step. Counting hits alone is **not** enough - that mistake cost a day earlier in this
project (`docs/settings-submenu.md` 4) and the key gap has to be long enough that one `pump` window
cannot absorb the next key (1200 ms was used; 500 ms was not enough and produced a misleading table).

### Stock's own nest (`nest_probe5.py`, Radio Settings > Voice Func > Battery Low)

| OK pressed on | resulting depth/mode | `0x0303699c` push | `0x030431cc` ENTER | `0x0304e560` commit-disp | `0x0303a770` | `0x0303a57c` |
|---|---|---|---|---|---|---|
| Settings | 2 / `0x0e` | 1 | 0 | 0 | 0 | 0 |
| Radio Settings | 3 / `0x15` | 1 | 0 | 0 | 0 | 0 |
| **Voice Func** (opens the child list) | 4 / `0x1c` | 1 | **1** | 0 | **1** | 0 |
| **Battery Low** (opens its values) | 5 / `0x23` | 1 | 0 | **1** | 0 | **1** |
| the committing OK | 4 / `0x1c` (popped) | 0 | 1 | 0 | 0 | 0 |

### Our own Channel Set rows (`nest_probe6.py`, and `chanset_rows_e2e.py --measure-all`)

| OK pressed on | `0x03047324` ENTER | `0x0304dc8c` COMMIT | `0x0304e5a0` (Radio Settings' arm) |
|---|---|---|---|
| a Channel Set row (opens its option level) | before 0, **FROM 1** | before 0, FROM 0 | 0 |
| inside that option level (commits) | before 1, FROM 0 | before 0, **FROM 1** | 0 |
| a child row of a nested child list | 0 | **1** | 0 |

**The rule, and it is the whole of the problem:** stock routes the OK of a **level-3 row** to the
per-category ENTER dispatcher (`0x03047324` for Channel Set), and the OK of **anything deeper** to the
per-category COMMIT dispatcher (`0x0304dc8c`). Both re-read the same level-3 row
(`0x3e685 + 2 + [0x3e4e8+3]`) and jump through the same relocated per-row tables, so a parent row of
ours is handed control at every depth below itself. "Commit" is simply what that second dispatcher
does for stock's own leaf rows, whose levels are all exactly one deep.

## 3. What was built, and what it did

`krk_nest.c` (in blob 2: the main blob had ~100 bytes of flash free before this work and 132 after it was
reverted, so the nesting code could never have lived there) with a `struct krk_kids`
(title, label array, row array, count) on a new `struct krk_screen.kids` field, reached from the main
blob through a vtable at a fixed address after `krk_ext_hooks`. Depth-aware bodies for the two
existing Channel Set stubs, no new enter or commit splice, plus one new header splice (section 4).

**Level 4, the child list: WORKS.** an earlier revision of `tests/chanset_rows_e2e.py` was green on all
of it, for both parents:

```
PASS  Scrambler: OK on Channel Set row 12 (item 24) opened a NESTED child list one level deeper:
      depth 4, mode 0x1c, 2 numbered rows ['Scrambler On/Off', 'Key'], index list [0, 1]
PASS  Scrambler: Back from the child list pops to the Channel Set rows (depth 3) with the cursor
      still on row 12 - the stock history pop needed no hook
PASS  Parrot: ... 5 numbered rows ['Parrot On/Off', 'Replay Audio', 'Check', 'Clear Audio', 'Status']
PASS  Parrot: Back ... cursor still on row 13
```

All it takes is `kids_enter()`: count, label array, subtype 2, visible rows, a sequential index list,
**icon mode 1**, cursor 0, slot 1 - the same shape as `krk_opt_enter` with icon 1 instead of 2. Up and
Down move inside it correctly and Back pops it with stock's own pop.

**Level 5, a child's option level: DOES NOT WORK.** `krk_opt_enter(kid->opt)` runs from the commit
dispatcher's case block (a breakpoint on `krk_opt_commit` and on `scr_on_set` confirms the chain
reaches both), and a cell dump every 250 ms afterwards shows:

```
afterChildOK+0..9   d=4 mode=0x1c icon=1 cnt=2 sub=2 cur=0 dot=0  labs=['Off', 'On']
```

The **label-array cell survives** - the option labels are there - and **every other cell it wrote is
gone**: the count, the cursor, the radio dot, the icon mode and the mode code are all the child list's
again, and the level is not pushed. Three things were tried and measured:

- Calling `history_queue_push()` ourselves: lands at **depth 6** with `0x3e4e8 = 00 01 01 0c 01 01`,
  the same cursor recorded twice, and the header dispatcher picks the handler of a level that does not
  exist. Stock pushes on every OK at every depth already.
- Adding `set_mode(0x23, 1)` (what stock's own depth-5 arms do): the mode does become `0x23` for a
  moment, and the level reaches depth 5 - but the cells are still overwritten.
- Latching which level is open, so a re-entry of our own handler cannot reinstall the child list over
  the option level: stops the label cell being touched, changes nothing else. A breakpoint confirms
  our enter body is **not** re-entered on those repaints, so the writer is not us.

The writer is in the path that runs after the commit dispatcher's case block returns, on the way back
out through `0x0304e5aa -> 0x0304e83a` and `0x0303a57c`'s tail `0x0303a76a`. `0x0303a57c` has a
hardcoded Channel Set arm at `0x0303a59c` (`[0x32ab9] = [0x3e5cc] + 16; [0x32ab8] = 2; [0x3e5d4] = 2`)
that writes the cells immediately around `OPT_ICON_MODE` (`0x32ab6`), and `0x32ab6` itself is written
at 43 sites image-wide. Making a new level stick from this context needs a splice in that shared
tail - a site every Settings level passes through, with no edge private to our rows. **That is
machinery out of proportion to the benefit, so it was not built.**

The asymmetry in one line: the ENTER dispatcher's case blocks are *item-actions* and their tail
(`0x0303d872 -> main_menu_screen_dispatch 0x0303b6c4`) draws the cells you installed; the COMMIT
dispatcher's case blocks are not, and their tail reconstructs the level instead.

## 4. The level-5 header, for whoever tries again

This part was solved and is worth keeping. The depth-4 child list needs **no** header hook: the
level-4 handler `0x03038720` special-cases category 5 and, for level-2 item 1, reads
`lrw a3,[0x30387c4]` = `0x030b89a0` (the Channel Set descriptor) then `desc[lang].ptr[row]`, so a
parent row's own label titles its child list - which is exactly what is wanted.

At depth 5 the header comes from `0x03038994`. Its category-5 / level-2-item-1 arm is `0x03038a7a`:

```
0x03038a7a  cmpnei r3,1 ; bt 0x03038e90        sub != 1 (not Channel Set)
            l3 = [0x3e685 + 2 + [0x3e4e8+3]]   the LEVEL-3 row
            cmpnei r3,3  ; bt 0x03038ab0
            cmpnei r3,7  ; bf 0x03038e94
            cmpnei r3,12 ; bf 0x03038e98
0x03038ab8  cmpnei r3,15 ; bt 0x03038e9c        <- rows 22.. land here
0x03038e9c  lrw r3,[0x03039018] = 0x030b1458 (the EMPTY string) ; br 0x03038ece
```

So stock knows only its own nested Channel Set rows `{3, 7, 12, 15}` and gives everything else a blank
header. The `bt16` at `0x03038aba` is the **only** branch to `0x03038e9c` anywhere in the function
(every 16-bit branch in `0x03038000..0x03039200` was scanned), so the 4 bytes at `0x03038ab8` are a
clean private splice site. A stub there gets `r3` = the level-3 row, has `r4` free as scratch (the
frame is `0x03038528`'s, which pushed r4/r15 and pops them at `0x03038eda`; r4 is written only in arms
this path never reaches), must keep the displaced `cmpnei r3,15` test for stock's own row 15, and must
not return - it jumps to the shared draw tail `0x03038ece` or to `0x03038abc`.

Because that header would be ours rather than a descriptor lookup, **a child's dynamic `title_fn`
would survive the nesting** - the Scrambler switch could still show the live key
(`Scrambler 00001234`). That is the one thing the flat Radio Settings conversion cannot keep
(`docs/settings-submenu.md` 3). In the flat Channel Set arrangement that ships, the header of a
Scrambler or Parrot row is likewise the static label from the relocated array, so the live-key title
is **not** shown there either; `scr_on_title()` stays in the source and is still used wherever
`krk_opt_title` resolves the row (the KRAKEN list path).

## 5. What ships instead

Nine flat rows appended to the stock Channel Set list as items 22..30, with the labels planned for the nest:

| item | label | kind | screen |
|---|---|---|---|
| 22 | DMR Link | screen | blob ui5 |
| 23 | Burst Voice | screen | blob ui4 |
| 24 | Scrambler On/Off | option level | `krk_scramble_on_screen` |
| 25 | Key | numeric entry | `krk_scrkey_row` |
| 26 | Parrot On/Off | option level | `krk_parrot_arm_screen` |
| 27 | Replay Audio | one-shot | `krk_parrot_play_screen` |
| 28 | Check | one-shot | `krk_parrot_check_screen` |
| 29 | Clear Audio | one-shot | `krk_parrot_clear_screen` |
| 30 | Status | screen | `krk_parrot_status_screen` |

Scrambler Fmt stays in the KRAKEN list: it is a key-format / interop knob, not part of the
planned `{ Scrambler On/Off, Key }` pair.

**Mode gating:** both features are digital-only, and the parent rows are left **always visible**. The
gates stay where they already are, in the engines (`scr_idle_reason()` reports idle unless
`krk_channel_dmr()`; Parrot's arming checks `krk_channel_dmr()`), so on an analog channel the row
opens and says why it will not do anything rather than vanishing. Hiding them instead would mean a new
arm in the stock generator's 21-arm visibility chain for each row, and a row that silently disappears
is worse for a user than one that explains itself.

## 6. Open, if anyone picks this up again

- The only thing missing is a way to make a new level's cells stick when the level is opened from the
  COMMIT dispatcher. The place to look is the tail after `0x0304e5aa`/`0x0303a76a`, and specifically
  whether `0x0303a57c`'s Channel Set arm at `0x0303a59c` can be made conditional on our rows. That
  arm is 20-odd bytes and is reached for every Channel Set level, so it needs the level-3 row test
  that `0x03038a7a` already demonstrates.
- A child whose row is a **screen** (`krk_run_screen`) rather than an option level was not the
  blocker: the runner parks the keypad and owns the display, so it does not depend on the cells
  surviving. A nest of one child list whose children are all blocking screens is probably reachable
  today; it was not pursued because four of the seven children wanted here are option or numeric rows.

## 7. 2026-11-06: the Channel Set nest works (Burst Voice)

With the corrected model the blocker of section 3 is gone, and Channel Set > Burst Voice is a parent with a child list whose
children open stock option levels that commit (`docs/chanset-submenu.md` 8 has the patch, `tests/bvnest_e2e.py` the check).

What section 3 got wrong, measured against the static trace:

- The "COMMIT dispatcher" `0x0304dc8c` is the **level-4 OK** handler of Channel Set, not a commit routine. For a row with a
  child list its job is `set_mode(0x23, 1)` and nothing else: stock's own nested row 15 has exactly that as its entry
  (`0x0304e254`). The 2026-11-03 build called `krk_opt_enter` from there instead.
- The cells of level 5 are written by the **level-5 render** `0x0303cb54`, which the engine runs after the OK handler returns
  (when the handler has set a new mode): `0x03036a48` (history push, cursor 0, slot 1) -> `0x0304547c` -> Channel Set's
  level-5 init `0x03045084`. For any row outside 3..15 that init exits at once (`cmphsi r2,13; bt 0x03045478` at
  `0x03045094`), so nothing installed the option level at the level the engine drew: cells written from the OK handler are
  either overwritten by the render or left half-valid, which is what the cell dumps of section 3 show.
- The `0x0303a57c` Channel Set arm (`0x0303a59c`) runs only while `[0x3fdb8]` bit 1 is set, a different mode of that handler;
  it was never in the path.

So the three splices the nest needs are the level-5 init bound at `0x03045094`, the level-5 OK chain's last test at
`0x0305031a` (`0x030500b0`, reached through `0x03036420` -> `0x03050414`) and the header test at `0x03038ab8` from section 4,
plus the parent's own two table entries. The level-4 child list is unchanged from section 3 except that it no longer writes
the cursor (the render's history push zeroes it on a fresh entry and the pop restores it after a child closes).

The same mechanism would serve the Scrambler and Parrot nests that section 5 shipped flat; they were not converted in this
change.

