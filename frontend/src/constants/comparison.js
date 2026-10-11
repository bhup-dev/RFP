export const COMPARISON_DIMENSIONS = Object.freeze([
  ['scope', 'Bid and scope'],
  ['products', 'Products and specifications'],
  ['commercial_terms', 'Commercial terms'],
  ['compliance', 'Compliance and documentation'],
  ['addenda', 'Addendum changes'],
])

export const COMPARISON_CATEGORIES = Object.freeze([
  'equivalent',
  'different',
  'missing',
  'conflicting',
  'not_comparable',
  'uncertain',
])

export const CATEGORY_HELP = Object.freeze({
  equivalent: 'The backend classified the compared values as equivalent.',
  different: 'The compared values differ.',
  missing: 'At least one bid has no source-supported value.',
  conflicting: 'At least one profile contains unresolved source conflict.',
  not_comparable: 'The backend could not make a meaningful comparison.',
  uncertain: 'The comparison requires further evidence or review.',
})
