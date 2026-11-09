# License

Required Notice: Copyright 2026 Alex Kaplan (https://github.com/awkaplan/dm32uv-kraken)

Except for the material listed under "What this license does not cover" below, the contents of
this repository are licensed under the **PolyForm Noncommercial License 1.0.0**, reproduced in
full at the end of this file. Under its "Notices" section, anyone who passes on any part of this
material must also pass on these terms (or their URL) and the `Required Notice:` line above.

This includes the update files built from this repository and published as release downloads,
as far as they contain this project's own code.

## What this license does not cover

The licensor can license only what the licensor owns. The following is not licensed under these
terms, and nothing in this repository grants any rights in it.

1. **Baofeng's firmware.** This repository does not contain Baofeng's firmware. The build takes
   the stock `DM32.00.01.048` image from
   [dm32uv-re](https://github.com/awkaplan/dm32uv-re) and patches it. Every image and update
   file the build produces (`out/`, release downloads) therefore contains Baofeng's firmware
   alongside this project's code. No license to Baofeng's firmware is granted or claimed. This
   project is not affiliated with, endorsed by, or supported by Baofeng.
2. **Facts and structure taken from the stock firmware.** The source code, linker scripts,
   manifests and notes in this repository name addresses, register values, data layouts and
   behaviour of the stock firmware, found by reverse engineering for interoperability. These
   terms cover this project's own code and writing, not Baofeng's firmware or any rights in it.
3. **Material from dm32uv-re.** The build uses tools, headers and data from
   [dm32uv-re](https://github.com/awkaplan/dm32uv-re), which has its own `LICENSE.md` (the same
   PolyForm Noncommercial terms for its own work, with its own exclusions).
4. **Third-party tools.** Roger Clark's DM32 tools
   ([github.com/rogerclarkmelbourne/DM32](https://github.com/rogerclarkmelbourne/DM32)), used to
   back up and install, are not included here and carry no license from their author. Get them
   from their own repository. Other projects named in [`CREDITS.md`](CREDITS.md) keep their own
   terms.
5. **Public specifications and data.** Standards and published data this project implements or
   uses (APRS, AX.25, Bell 202, SGP4, NIST SP 800-90A, the CelesTrak orbit data in `data/tles.txt`)
   belong to their authors.

---

# PolyForm Noncommercial License 1.0.0

<https://polyformproject.org/licenses/noncommercial/1.0.0>

## Acceptance

In order to get any license under these terms, you must agree
to them as both strict obligations and conditions to all
your licenses.

## Copyright License

The licensor grants you a copyright license for the
software to do everything you might do with the software
that would otherwise infringe the licensor's copyright
in it for any permitted purpose.  However, you may
only distribute the software according to [Distribution
License](#distribution-license) and make changes or new works
based on the software according to [Changes and New Works
License](#changes-and-new-works-license).

## Distribution License

The licensor grants you an additional copyright license
to distribute copies of the software.  Your license
to distribute covers distributing the software with
changes and new works permitted by [Changes and New Works
License](#changes-and-new-works-license).

## Notices

You must ensure that anyone who gets a copy of any part of
the software from you also gets a copy of these terms or the
URL for them above, as well as copies of any plain-text lines
beginning with `Required Notice:` that the licensor provided
with the software.  For example:

> Required Notice: Copyright Yoyodyne, Inc. (http://example.com)

## Changes and New Works License

The licensor grants you an additional copyright license to
make changes and new works based on the software for any
permitted purpose.

## Patent License

The licensor grants you a patent license for the software that
covers patent claims the licensor can license, or becomes able
to license, that you would infringe by using the software.

## Noncommercial Purposes

Any noncommercial purpose is a permitted purpose.

## Personal Uses

Personal use for research, experiment, and testing for
the benefit of public knowledge, personal study, private
entertainment, hobby projects, amateur pursuits, or religious
observance, without any anticipated commercial application,
is use for a permitted purpose.

## Noncommercial Organizations

Use by any charitable organization, educational institution,
public research organization, public safety or health
organization, environmental protection organization,
or government institution is use for a permitted purpose
regardless of the source of funding or obligations resulting
from the funding.

## Fair Use

You may have "fair use" rights for the software under the
law. These terms do not limit them.

## No Other Rights

These terms do not allow you to sublicense or transfer any of
your licenses to anyone else, or prevent the licensor from
granting licenses to anyone else.  These terms do not imply
any other licenses.

## Patent Defense

If you make any written claim that the software infringes or
contributes to infringement of any patent, your patent license
for the software granted under these terms ends immediately. If
your company makes such a claim, your patent license ends
immediately for work on behalf of your company.

## Violations

The first time you are notified in writing that you have
violated any of these terms, or done anything with the software
not covered by your licenses, your licenses can nonetheless
continue if you come into full compliance with these terms,
and take practical steps to correct past violations, within
32 days of receiving notice.  Otherwise, all your licenses
end immediately.

## No Liability

***As far as the law allows, the software comes as is, without
any warranty or condition, and the licensor will not be liable
to you for any damages arising out of these terms or the use
or nature of the software, under any kind of legal claim.***

## Definitions

The **licensor** is the individual or entity offering these
terms, and the **software** is the software the licensor makes
available under these terms.

**You** refers to the individual or entity agreeing to these
terms.

**Your company** is any legal entity, sole proprietorship,
or other kind of organization that you work for, plus all
organizations that have control over, are under the control of,
or are under common control with that organization.  **Control**
means ownership of substantially all the assets of an entity,
or the power to direct its management and policies by vote,
contract, or otherwise.  Control can be direct or indirect.

**Your licenses** are all the licenses granted to you for the
software under these terms.

**Use** means anything you do with the software requiring one
of your licenses.
