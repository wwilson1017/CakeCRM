/** True when a text box holds more than it shows, which is when a bigger window helps (#305). */
export function overflows(el: { scrollHeight: number; clientHeight: number }): boolean {
  return el.scrollHeight > el.clientHeight + 1;
}
