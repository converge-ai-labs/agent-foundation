/**
 * The one mark for a value the service never reported. Sessions, Traces and
 * cost formatting all read it from here so an unavailable value never looks
 * like a different kind of gap on a neighbouring surface — and never like zero.
 */
export const UNKNOWN = "—";
