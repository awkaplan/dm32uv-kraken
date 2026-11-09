/*
 * krk_config.h - build-time settings (no runtime storage: checklist P4). Edit, rebuild, reflash.
 */
#ifndef KRK_CONFIG_H
#define KRK_CONFIG_H

/* F8 analog APRS beacon. The callsign and SSID are set on the radio (KRAKEN > APRS Callsign);
 * the beacon refuses to transmit until one is saved. */
#define KRK_APRS_DEST     "APZKRK"          /* APZ = experimental software tocall */
#define KRK_APRS_PATH1    "WIDE1-1"
#define KRK_APRS_PATH2    "WIDE2-1"
#define KRK_APRS_SYMTAB   '/'
#define KRK_APRS_SYMBOL   '['               /* '[' = person, '>' = car */
#define KRK_APRS_COMMENT  "DM-32UV Kraken"

#endif
