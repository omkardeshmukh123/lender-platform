import { MetadataRoute } from 'next'

const BASE = 'https://lender-platform.vercel.app'

const STATES = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh',
  'Goa','Gujarat','Haryana','Himachal Pradesh','Jharkhand','Karnataka',
  'Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya','Mizoram',
  'Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu',
  'Telangana','Tripura','Uttar Pradesh','Uttarakhand','West Bengal',
  'Delhi','Jammu & Kashmir','Ladakh','Puducherry','Chandigarh',
]

const LOAN_TYPES = [
  'MSME Loan','Personal Loan','Home Loan','Business Loan','Vehicle Loan',
  'Gold Loan','Education Loan','Micro Loan','Loan Against Property',
  'Working Capital','Agriculture Loan','EV Loan','Two Wheeler Loan',
  'Rural Loan','Microfinance','Supply Chain Finance','Consumer Durable Loan','Credit Card',
]

export default function sitemap(): MetadataRoute.Sitemap {
  const statePages = STATES.map(s => ({
    url: `${BASE}/lenders/${encodeURIComponent(s)}`,
    lastModified: new Date(),
    changeFrequency: 'weekly' as const,
    priority: 0.8,
  }))

  const loanTypePages = LOAN_TYPES.map(t => ({
    url: `${BASE}/lenders/${encodeURIComponent(t)}`,
    lastModified: new Date(),
    changeFrequency: 'weekly' as const,
    priority: 0.8,
  }))

  return [
    { url: BASE, lastModified: new Date(), changeFrequency: 'daily', priority: 1 },
    { url: `${BASE}/dashboard`, lastModified: new Date(), changeFrequency: 'daily', priority: 0.9 },
    { url: `${BASE}/verify`, lastModified: new Date(), changeFrequency: 'weekly', priority: 0.7 },
    ...statePages,
    ...loanTypePages,
  ]
}
