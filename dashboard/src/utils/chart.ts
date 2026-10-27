/** Round gridlines for a count axis: a 1, 2 or 5 step, at most five lines. */
export function countTicks(max: number): number[] {
  const rough = Math.max(max, 1) / 4
  const magnitude = 10 ** Math.floor(Math.log10(rough))
  const step = Math.max(1, [1, 2, 5, 10].map((factor) => factor * magnitude).find((value) => value >= rough) ?? 1)
  const top = Math.max(step, Math.ceil(max / step) * step)
  return Array.from({ length: top / step + 1 }, (_, index) => index * step)
}
