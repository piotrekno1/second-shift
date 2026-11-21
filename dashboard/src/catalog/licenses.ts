// Explore lists a skill only under one of these licenses. Listing a skill
// recommends it, so its terms must let anyone run it on their own code.
// CC-BY-4.0 covers repositories that license the skill text apart from their
// code. It asks for credit, which the card gives by naming the publisher.
// Names are SPDX identifiers, as GitHub reports them.
export const ALLOWED_LICENSES: readonly string[] = [
  'MIT',
  'Apache-2.0',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'ISC',
  'CC-BY-4.0',
]
