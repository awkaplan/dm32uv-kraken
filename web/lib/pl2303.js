// A Prolific PL2303 cable driven directly over WebUSB, shaped like a Web Serial port so Link can use it.
//
// Why: on a Mac, Apple's own driver for this chip (AppleUSBPLCOM) cannot set a line rate the way browsers
// ask for one. Chrome sets any rate above 38400 with the IOSSIOSPEED call; the driver's handler for it
// (HwProgramBaudRate) passes its arguments on in the wrong order, the chip is told "8 baud", and falls back
// to 9600. So a port opened at 115200 really runs at 9600 and the radio is never heard (measured
// 2026-11-09, macOS 26; docs/web-flasher.md section 5). The driver only holds the USB interface while its
// serial port is open, so with the port closed the page can claim the interface and run the chip itself.
//
// The protocol is the chip's ordinary one (PL2303 H/HX/TA/TB families): a vendor start-up sequence, CDC
// SET_LINE_CODING for the rate (a plain number for the rates the chip knows, else a divisor code), and two
// bulk endpoints for the bytes.

export const PROLIFIC_VENDOR = 0x067b;

const RATES = [75, 150, 300, 600, 1200, 1800, 2400, 3600, 4800, 7200, 9600, 14400, 19200, 28800, 38400, 57600,
  115200, 230400, 460800, 614400, 921600, 1228800, 2457600, 3000000, 6000000];
const READ_DEPTH = 8; // bulk-in reads kept waiting

// What SET_LINE_CODING carries for a rate: the rate itself if the chip knows it, else
// 0x80000000 | exponent << 9 | mantissa, for 12 MHz * 32 / (mantissa * 4^exponent).
export function rateCode(baud) {
  if (RATES.includes(baud)) return baud;
  let mantissa = Math.max(1, Math.floor(384e6 / baud));
  let exponent = 0;
  while (mantissa >= 512) {
    if (exponent === 7) {
      mantissa = 511;
      break;
    }
    mantissa >>= 2;
    exponent++;
  }
  return (0x80000000 | (exponent << 9) | mantissa) >>> 0;
}

const equal = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

export class Pl2303Port {
  constructor(device) {
    this.dev = device;
    this.readable = null;
    this.writable = null;
    this.started = false;
  }

  getInfo() {
    return { usbVendorId: this.dev.vendorId, usbProductId: this.dev.productId, direct: true };
  }

  async vendorRead(value) {
    const r = await this.dev.controlTransferIn({ requestType: "vendor", recipient: "device", request: 1, value, index: 0 }, 1);
    if (r.status !== "ok") throw new Error(`cable chip refused a read (${r.status})`);
  }

  async vendorWrite(value, index) {
    const r = await this.dev.controlTransferOut({ requestType: "vendor", recipient: "device", request: 1, value, index });
    if (r.status !== "ok") throw new Error(`cable chip refused a write (${r.status})`);
  }

  async open({ baudRate }) {
    const d = this.dev;
    if (!d.opened) await d.open();
    if (!d.configuration) await d.selectConfiguration(1);
    const intf = d.configuration.interfaces[0];
    const eps = intf.alternate.endpoints;
    const epIn = eps.find((e) => e.type === "bulk" && e.direction === "in");
    const epOut = eps.find((e) => e.type === "bulk" && e.direction === "out");
    if (!epIn || !epOut) throw new Error("not a PL2303 serial cable");
    this.intf = intf.interfaceNumber;
    await d.claimInterface(this.intf); // fails while another driver has the cable's port open
    if (!this.started) {
      // the chip's start-up sequence; the last write differs on the oldest (CDC-class) chips
      await this.vendorRead(0x8484);
      await this.vendorWrite(0x0404, 0);
      await this.vendorRead(0x8484);
      await this.vendorRead(0x8383);
      await this.vendorRead(0x8484);
      await this.vendorWrite(0x0404, 1);
      await this.vendorRead(0x8484);
      await this.vendorRead(0x8383);
      await this.vendorWrite(0, 1);
      await this.vendorWrite(1, 0);
      await this.vendorWrite(2, d.deviceClass === 0x02 ? 0x24 : 0x44);
      this.started = true;
    }
    await this.vendorWrite(8, 0); // empty both data pipes
    await this.vendorWrite(9, 0);
    const iface = { requestType: "class", recipient: "interface", index: this.intf };
    const code = rateCode(baudRate);
    const coding = new Uint8Array([code & 0xff, (code >>> 8) & 0xff, (code >>> 16) & 0xff, code >>> 24, 0, 0, 8]); // 8N1
    const set = await d.controlTransferOut({ ...iface, request: 0x20, value: 0 }, coding);
    const got = await d.controlTransferIn({ ...iface, request: 0x21, value: 0 }, 7);
    if (set.status !== "ok" || got.status !== "ok" || !equal(new Uint8Array(got.data.buffer, got.data.byteOffset, got.data.byteLength), coding)) {
      throw new Error(`cable chip did not take ${baudRate} baud`);
    }
    await d.controlTransferOut({ ...iface, request: 0x22, value: 3 }); // DTR and RTS on, as an open port has them

    this.closing = false;
    const port = this;
    this.readable = new ReadableStream({
      start(controller) {
        port.controller = controller;
        queueMicrotask(() => port.startReads(controller, epIn.endpointNumber, epIn.packetSize));
      },
      cancel() {
        port.closing = true;
      },
    });
    this.writable = new WritableStream({
      async write(chunk) {
        const r = await d.transferOut(epOut.endpointNumber, chunk);
        if (r.status !== "ok") throw new Error(`cable write failed (${r.status})`);
      },
    });
  }

  // The chip hands over what it has each time it is asked, usually a byte or two, and holds only about
  // 256 bytes itself: so several one-packet reads are always waiting. macOS does not fill waiting reads in
  // the order they were asked for, but each finishes as its packet arrives, so they are taken as they
  // finish (measured: in order and complete at 115200).
  startReads(controller, ep, size) {
    const d = this.dev;
    const ask = () =>
      d.transferIn(ep, size).then(
        async (r) => {
          if (this.closing) return;
          if (r.status === "stall") await d.clearHalt("in", ep).catch(() => {});
          if (r.data && r.data.byteLength) controller.enqueue(new Uint8Array(r.data.buffer, r.data.byteOffset, r.data.byteLength));
          ask();
        },
        (e) => {
          if (this.closing || !this.readable) return;
          this.readable = null; // the cable is gone (or was taken away): Link reports it
          try {
            controller.error(e);
          } catch {}
        },
      );
    for (let i = 0; i < READ_DEPTH; i++) ask();
  }

  async close() {
    this.closing = true;
    try {
      await this.dev.close(); // ends the waiting transfers and gives the interface back
    } catch {}
    try {
      this.controller.close();
    } catch {}
    this.readable = null;
    this.writable = null;
  }
}
