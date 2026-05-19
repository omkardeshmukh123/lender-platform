import Link from 'next/link'
import {
  ArrowLeft, Building2, MapPin, TrendingUp, FileText,
  Users, Shield, BarChart2, Info,
} from 'lucide-react'

interface FieldDef {
  name: string
  label: string
  what: string
  why: string
  example?: string
}

interface Section {
  title: string
  icon: React.ElementType
  color: string
  bg: string
  fields: FieldDef[]
}

const SECTIONS: Section[] = [
  {
    title: 'Identity & Classification',
    icon: Building2,
    color: '#1A7070',
    bg: '#E6F4F4',
    fields: [
      {
        name: 'company_name',
        label: 'Company Name',
        what: 'The legal registered name of the lending institution.',
        why: 'Primary identifier used to search, reference, and compare lenders. Always matches the name on RBI / MCA21 records.',
        example: 'HDFC Bank Ltd, Bajaj Finance Ltd',
      },
      {
        name: 'company_type',
        label: 'Company Type',
        what: 'Broad classification of the lender — NBFC, PSU Bank, Private Bank, Small Finance Bank, Cooperative Bank, etc.',
        why: 'Determines the regulatory framework the lender operates under, its risk profile, and the kinds of products it is permitted to offer.',
        example: 'NBFC, PSU Bank, Private Bank, Small Finance Bank',
      },
      {
        name: 'rbi_category',
        label: 'RBI Category',
        what: 'The specific registration category assigned by the Reserve Bank of India.',
        why: 'Tells you exactly which RBI regulations apply and what lending activities are permitted. NBFC-MFI focus on micro-loans; NBFC-HFC on housing; NBFC-ICC on general credit.',
        example: 'NBFC-MFI, NBFC-HFC, NBFC-ICC, NBFC-IDF',
      },
      {
        name: 'cin',
        label: 'CIN (Corporate ID)',
        what: 'Corporate Identification Number assigned by the Ministry of Corporate Affairs (MCA).',
        why: 'Unique legal identifier. You can verify the company directly on the MCA21 portal using this number to confirm its legal standing.',
        example: 'U65910MH2000PLC128203',
      },
      {
        name: 'is_listed',
        label: 'Listed on Exchange',
        what: 'Whether the company is publicly listed on BSE or NSE.',
        why: 'Listed lenders are subject to SEBI disclosure requirements, making their financials and governance more transparent and independently audited.',
        example: 'Yes (NSE: BAJFINANCE), No',
      },
      {
        name: 'stock_symbol',
        label: 'Stock Symbol',
        what: 'The ticker symbol used on stock exchanges.',
        why: 'Lets you quickly pull up the lender\'s financial performance, investor reports, and public disclosures.',
        example: 'BAJFINANCE, HDFCBANK, SBIN',
      },
    ],
  },
  {
    title: 'Location & Geographic Reach',
    icon: MapPin,
    color: '#0284C7',
    bg: '#F0F9FF',
    fields: [
      {
        name: 'hq_location',
        label: 'HQ City',
        what: 'City where the lender\'s main headquarters is located.',
        why: 'Indicates the primary operating base. Useful when you need in-person visits to the head office for documentation or escalations.',
        example: 'Mumbai, Bengaluru, Pune',
      },
      {
        name: 'hq_state',
        label: 'HQ State',
        what: 'State in which the headquarters is registered.',
        why: 'Certain state-level regulations and court jurisdictions apply based on where a lender is headquartered.',
        example: 'Maharashtra, Karnataka, Tamil Nadu',
      },
      {
        name: 'pan_india',
        label: 'Pan India',
        what: 'Indicates the lender operates across all states and union territories in India.',
        why: 'Confirms you can apply regardless of your location in India — no state-level eligibility barrier.',
        example: 'Yes / No',
      },
      {
        name: 'operating_states',
        label: 'Operating States',
        what: 'The specific states where the lender has an active presence and can disburse loans.',
        why: 'Critical check before applying — if your state is not in this list, you may be ineligible even if you meet all other criteria.',
        example: 'Maharashtra, Gujarat, Rajasthan…',
      },
      {
        name: 'operating_intensity',
        label: 'Operating Reach',
        what: 'A qualitative label describing how widely the lender operates — Local, Regional, or National.',
        why: 'Quick way to gauge whether a lender is a hyperlocal co-operative or a nationwide institution without counting individual states.',
        example: 'Local, Regional, National',
      },
    ],
  },
  {
    title: 'Financial Scale',
    icon: TrendingUp,
    color: '#A07E1A',
    bg: '#FDF8E4',
    fields: [
      {
        name: 'aum_crores',
        label: 'AUM — Assets Under Management',
        what: 'Total value of all loans currently outstanding on the lender\'s books, measured in crores (₹).',
        why: 'The single most important indicator of a lender\'s size and lending capacity. Higher AUM means more capital deployed, better ability to absorb defaults, and usually faster processing at scale.',
        example: '₹1,200 Cr (mid-size NBFC), ₹8.5L Cr (large bank)',
      },
      {
        name: 'aum_category',
        label: 'AUM Category',
        what: 'A bucket/tier label derived from the AUM figure — Small, Medium, Large, Very Large.',
        why: 'Allows instant size comparison without parsing raw numbers. Useful when filtering for lenders of a specific scale.',
        example: 'Small (< ₹500 Cr), Medium, Large, Very Large (> ₹10,000 Cr)',
      },
      {
        name: 'last_year_revenue',
        label: 'Last Year Revenue',
        what: 'Total revenue earned by the lender in the most recent financial year, in crores.',
        why: 'Reflects business health and operational volume. A lender with growing revenue is expanding its book, while declining revenue may signal stress.',
        example: '₹340 Cr',
      },
      {
        name: 'authorized_capital_lakhs',
        label: 'Authorized Capital',
        what: 'The maximum amount of share capital the company is legally permitted to raise, in lakhs.',
        why: 'Sets the growth ceiling — a company can only expand its equity base up to this limit without changing its Articles of Association.',
        example: '₹500 L (₹5 Cr)',
      },
      {
        name: 'paid_up_capital_lakhs',
        label: 'Paid-up Capital',
        what: 'The actual amount of capital shareholders have paid into the company, in lakhs.',
        why: 'Real money in the company. Higher paid-up capital relative to AUM indicates a well-capitalized lender with more cushion against defaults.',
        example: '₹200 L (₹2 Cr)',
      },
      {
        name: 'recent_funding',
        label: 'Recent Funding Round',
        what: 'Description of the most recent equity or debt fundraise (e.g., Series B, NCD issue).',
        why: 'Shows investor confidence and the lender\'s growth trajectory. A recent fundraise often means the lender has fresh capital to deploy.',
        example: 'Series C from Sequoia, NCD issue ₹500 Cr',
      },
      {
        name: 'recent_funding_amount',
        label: 'Funding Amount',
        what: 'Value of the most recent funding round in crores.',
        why: 'Quantifies the capital infusion — larger raises typically mean the lender can grow its book faster.',
        example: '₹250 Cr',
      },
      {
        name: 'recent_funding_year',
        label: 'Funding Year',
        what: 'Year when the most recent fundraise closed.',
        why: 'Recency matters — a 2024 fundraise is far more relevant than one from 2018.',
        example: '2023, 2024',
      },
    ],
  },
  {
    title: 'Products & Lending',
    icon: FileText,
    color: '#16A34A',
    bg: '#F0FDF4',
    fields: [
      {
        name: 'primary_loan_segments',
        label: 'Primary Loan Segments',
        what: 'The main categories of loans this lender specializes in.',
        why: 'Each lender has strengths in specific segments. An NBFC-MFI excels at micro-loans; an HFC at home loans. Matching the segment to your need improves approval chances.',
        example: 'Home Loan, MSME Loan, Personal Loan, Gold Loan',
      },
      {
        name: 'policy_count',
        label: 'Policy Count',
        what: 'Number of distinct loan products or schemes for which detailed terms are available on the platform.',
        why: 'More policies mean more options. A lender with 10 policies gives you more products to compare than one with 2.',
        example: '4 policies, 12 policies',
      },
      {
        name: 'min_interest_rate',
        label: 'Min Interest Rate',
        what: 'The lowest interest rate (% p.a.) offered by this lender across all its products.',
        why: 'Quick comparison baseline. The actual rate offered to you depends on your profile, but this tells you the floor — the best-case rate you could qualify for.',
        example: '8.5% p.a., 12% p.a.',
      },
      {
        name: 'business_sector',
        label: 'Business Sector',
        what: 'The industry or borrower segment the lender primarily focuses on.',
        why: 'Sector-focused lenders understand the cash flow patterns and risks of their segment better, often leading to faster approvals and more flexible terms.',
        example: 'Retail, MSME, Agriculture, Microfinance',
      },
    ],
  },
  {
    title: 'Operations',
    icon: Users,
    color: '#7C3AED',
    bg: '#FAF5FF',
    fields: [
      {
        name: 'established_year',
        label: 'Established Year',
        what: 'The year the company was incorporated or began operations.',
        why: 'Older institutions carry a longer track record of surviving economic cycles. A lender established in 1994 has navigated multiple recessions; a 2022 startup has not.',
        example: '1994, 2010, 2019',
      },
      {
        name: 'employee_count',
        label: 'Employee Count',
        what: 'Total number of people employed by the lender.',
        why: 'A proxy for operational capacity. More employees usually means more branches, faster processing, and larger support teams — especially relevant for complex loans.',
        example: '250 employees, 12,000 employees',
      },
      {
        name: 'branch_count',
        label: 'Branch Count',
        what: 'Number of physical branch offices across India.',
        why: 'Important if you prefer in-person service. Also correlates with geographic coverage — more branches often means better penetration in smaller cities and rural areas.',
        example: '18 branches, 850 branches',
      },
    ],
  },
  {
    title: 'Compliance & Governance',
    icon: Shield,
    color: '#DC2626',
    bg: '#FEF2F2',
    fields: [
      {
        name: 'company_status',
        label: 'Company Status (MCA21)',
        what: 'The legal operational status of the company as recorded in the MCA21 registry.',
        why: 'Critical safety check. A "Struck Off" or "Dissolved" status means the company has been deregistered — do not engage with such a lender. "Active" confirms the entity is legally operational.',
        example: 'Active, Struck Off, Dormant, Dissolved',
      },
      {
        name: 'mca21_status',
        label: 'MCA21 Status',
        what: 'A secondary status field from the MCA21 corporate registry, sometimes more granular than company_status.',
        why: 'Cross-verification from the government\'s own registry. Useful when company_status alone doesn\'t give full picture.',
        example: 'Active, Under Process',
      },
      {
        name: 'grievance_officer',
        label: 'Grievance Officer',
        what: 'Name, designation, email, and phone of the officer designated to handle customer complaints, as mandated by RBI.',
        why: 'RBI requires all regulated lenders to appoint a Grievance Redressal Officer. If you face issues — mis-selling, wrongful charges, harassment — this is your first formal escalation point before approaching RBI Ombudsman.',
        example: 'Rahul Sharma, Compliance Head, grievance@lender.com',
      },
    ],
  },
  {
    title: 'Data Quality & Metadata',
    icon: BarChart2,
    color: '#6B7280',
    bg: '#F9FAFB',
    fields: [
      {
        name: 'quality_score',
        label: 'Data Quality Score',
        what: 'An internal score (0–100%) reflecting how complete and verified the lender\'s data is on this platform.',
        why: 'Helps you gauge how much to trust the data shown. A score of 85% means most fields are populated and verified; 30% means many fields are missing or unconfirmed.',
        example: '92% (high confidence), 35% (limited data)',
      },
      {
        name: 'data_source',
        label: 'Data Source',
        what: 'Where the data was obtained — MCA21 registry, RBI disclosures, lender website, financial filings, etc.',
        why: 'Transparency about provenance. Government registry data (MCA21, RBI) is highly reliable; web-scraped data may need independent verification.',
        example: 'MCA21 + RBI website, Annual Report',
      },

    ],
  },
]

export default function GuidePage() {
  return (
    <div className="min-h-screen" style={{ background: '#F7FAFA' }}>

      {/* Nav */}
      <nav className="bg-white sticky top-0 z-30" style={{ borderBottom: '1px solid #D8EBEB' }}>
        <div className="max-w-4xl mx-auto px-4 sm:px-6 py-3.5 flex items-center gap-3">
          <Link
            href="/dashboard"
            className="p-1.5 rounded-lg transition-colors"
            style={{ color: '#7A9E9E' }}
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <span className="text-sm font-semibold" style={{ color: '#0D3333' }}>
            Lender Data Field Guide
          </span>
        </div>
      </nav>

      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-10">

        {/* Hero */}
        <div className="mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold mb-4"
               style={{ background: '#E6F4F4', color: '#1A7070' }}>
            <Info className="w-3.5 h-3.5" />
            Reference Guide
          </div>
          <h1 className="text-3xl font-bold mb-3" style={{ color: '#0D3333' }}>
            What every field means
          </h1>
          <p className="text-base leading-relaxed max-w-2xl" style={{ color: '#3D6363' }}>
            Every data point shown on a lender&apos;s profile is explained below — what it is,
            why it matters, and how to use it when evaluating a lender.
          </p>
        </div>

        {/* Sections */}
        <div className="space-y-10">
          {SECTIONS.map(section => {
            const Icon = section.icon
            return (
              <div key={section.title}>
                {/* Section header */}
                <div className="flex items-center gap-2.5 mb-5">
                  <div className="p-1.5 rounded-lg" style={{ background: section.bg }}>
                    <Icon className="w-4 h-4" style={{ color: section.color }} />
                  </div>
                  <h2 className="text-lg font-bold" style={{ color: '#0D3333' }}>
                    {section.title}
                  </h2>
                </div>

                {/* Fields */}
                <div className="space-y-3">
                  {section.fields.map(field => (
                    <div
                      key={field.name}
                      className="bg-white rounded-2xl border border-gray-100 p-5"
                    >
                      <div className="flex flex-col sm:flex-row sm:items-start gap-4">

                        {/* Field name badge */}
                        <div className="flex-shrink-0">
                          <span
                            className="inline-block px-2.5 py-1 rounded-lg text-xs font-mono font-semibold"
                            style={{ background: section.bg, color: section.color }}
                          >
                            {field.name}
                          </span>
                        </div>

                        <div className="flex-1 min-w-0">
                          <h3 className="text-sm font-bold text-gray-900 mb-2">
                            {field.label}
                          </h3>

                          <div className="space-y-2">
                            <div>
                              <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                                What it is
                              </span>
                              <p className="text-sm text-gray-700 mt-0.5 leading-relaxed">
                                {field.what}
                              </p>
                            </div>

                            <div>
                              <span className="text-[11px] font-semibold uppercase tracking-wide"
                                    style={{ color: section.color }}>
                                Why it matters
                              </span>
                              <p className="text-sm mt-0.5 leading-relaxed" style={{ color: '#3D5050' }}>
                                {field.why}
                              </p>
                            </div>

                            {field.example && (
                              <div className="pt-1">
                                <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                                  Example
                                </span>
                                <p className="text-xs text-gray-500 mt-0.5 font-mono">
                                  {field.example}
                                </p>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>

        {/* Footer CTA */}
        <div className="mt-12 rounded-2xl p-6 text-center"
             style={{ background: 'linear-gradient(135deg,#0F4848,#1A7070)' }}>
          <p className="text-white font-semibold mb-1">Ready to find a lender?</p>
          <p className="text-sm mb-4" style={{ color: '#A8DADA' }}>
            Use filters on the dashboard to match lenders to your exact requirements.
          </p>
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-all hover:-translate-y-0.5"
            style={{ background: 'white', color: '#0F4848' }}
          >
            Browse Lenders
          </Link>
        </div>

      </div>
    </div>
  )
}
