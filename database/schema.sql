-- ============================================================
-- LENDER DISCOVERY PLATFORM - DATABASE SCHEMA (FIXED)
-- Based on sir's exact requirements
-- ============================================================

CREATE TABLE lenders (
  -- Internal
  id                    BIGSERIAL PRIMARY KEY,
  created_at            TIMESTAMP DEFAULT NOW(),
  last_updated          TIMESTAMP DEFAULT NOW(),
  
  -- EXACT FIELDS FROM SIR'S REQUIREMENTS
  company_name          TEXT NOT NULL,
  company_type          TEXT NOT NULL,
  aum_crores            NUMERIC,
  product_types         JSONB,
  primary_product       TEXT,
  established_year      INTEGER,
  hq_location           TEXT,
  employee_count        INTEGER,
  ticket_size_min       NUMERIC,
  ticket_size_max       NUMERIC,
  has_subsidiaries      BOOLEAN,
  
  -- Additional (for filtering/contact)
  hq_state              TEXT,
  website               TEXT,
  phone                 TEXT,
  email                 TEXT
);

-- Indexes for fast filtering
CREATE INDEX idx_lenders_company_type ON lenders(company_type);
CREATE INDEX idx_lenders_hq_state ON lenders(hq_state);
CREATE INDEX idx_lenders_aum ON lenders(aum_crores);
CREATE INDEX idx_lenders_established ON lenders(established_year);
CREATE INDEX idx_lenders_products ON lenders USING GIN(product_types);

-- Enable Row Level Security
ALTER TABLE lenders ENABLE ROW LEVEL SECURITY;

-- Policy: Allow public read access
CREATE POLICY "Allow public read access"
  ON lenders
  FOR SELECT
  TO anon
  USING (true);

-- ============================================================
-- SAMPLE DATA (15 companies for demo)
-- ============================================================

INSERT INTO lenders (
  company_name, company_type, aum_crores, product_types, 
  primary_product, established_year, hq_location, hq_state,
  employee_count, ticket_size_min, ticket_size_max, 
  has_subsidiaries, website
) VALUES
  -- NBFCs
  (
    '121 Finance', 'NBFC', 250, 
    '["MSME Loan", "Working Capital", "Invoice Discounting"]'::jsonb,
    'Trade Credit Finance', 2017, 'Jaipur, Rajasthan', 'Rajasthan',
    150, 5, 200, false, 'https://121finance.com'
  ),
  (
    '4Fin Finance', 'NBFC', 180,
    '["MSME Loan", "Working Capital", "Business Loan"]'::jsonb,
    'MSME Lending', 2019, 'Mumbai, Maharashtra', 'Maharashtra',
    120, 10, 500, false, 'https://4fin.in'
  ),
  (
    'Bajaj Finance', 'NBFC', 250000,
    '["Personal Loan", "Home Loan", "Business Loan", "Consumer Durable Loan"]'::jsonb,
    'Consumer Finance', 2007, 'Pune, Maharashtra', 'Maharashtra',
    35000, 1, 2500, true, 'https://www.bajajfinserv.in'
  ),
  (
    'Muthoot Finance', 'NBFC', 60000,
    '["Gold Loan", "Personal Loan"]'::jsonb,
    'Gold Loan', 1939, 'Kochi, Kerala', 'Kerala',
    25000, 0.1, 50, true, 'https://www.muthootfinance.com'
  ),
  (
    'Shriram Finance', 'NBFC', 185000,
    '["Vehicle Loan", "MSME Loan", "Personal Loan", "Gold Loan"]'::jsonb,
    'Vehicle Finance', 1979, 'Chennai, Tamil Nadu', 'Tamil Nadu',
    48000, 1, 2000, true, 'https://www.shriramfinance.in'
  ),
  
  -- PSU Banks
  (
    'State Bank of India', 'PSU Bank', 4700000,
    '["Home Loan", "Personal Loan", "Business Loan", "Vehicle Loan", "Education Loan"]'::jsonb,
    'Full Banking Services', 1955, 'Mumbai, Maharashtra', 'Maharashtra',
    245000, 1, 50000, true, 'https://www.onlinesbi.sbi'
  ),
  (
    'Punjab National Bank', 'PSU Bank', 1100000,
    '["Home Loan", "Personal Loan", "Business Loan", "Agriculture Loan"]'::jsonb,
    'Full Banking Services', 1894, 'New Delhi, Delhi', 'Delhi',
    180000, 1, 20000, true, 'https://www.pnbindia.in'
  ),
  (
    'Bank of Baroda', 'PSU Bank', 1300000,
    '["Home Loan", "Personal Loan", "Business Loan", "MSME Loan"]'::jsonb,
    'Full Banking Services', 1908, 'Vadodara, Gujarat', 'Gujarat',
    85000, 1, 25000, true, 'https://www.bankofbaroda.in'
  ),
  
  -- Private Banks
  (
    'HDFC Bank', 'Private Bank', 2100000,
    '["Home Loan", "Personal Loan", "Business Loan", "Vehicle Loan", "Credit Card"]'::jsonb,
    'Full Banking Services', 1994, 'Mumbai, Maharashtra', 'Maharashtra',
    120000, 1, 100000, false, 'https://www.hdfcbank.com'
  ),
  (
    'ICICI Bank', 'Private Bank', 1900000,
    '["Home Loan", "Personal Loan", "Business Loan", "Vehicle Loan"]'::jsonb,
    'Full Banking Services', 1994, 'Mumbai, Maharashtra', 'Maharashtra',
    105000, 1, 75000, false, 'https://www.icicibank.com'
  ),
  (
    'Axis Bank', 'Private Bank', 1000000,
    '["Home Loan", "Personal Loan", "Business Loan", "MSME Loan"]'::jsonb,
    'Full Banking Services', 1993, 'Mumbai, Maharashtra', 'Maharashtra',
    75000, 1, 50000, false, 'https://www.axisbank.com'
  ),
  (
    'Kotak Mahindra Bank', 'Private Bank', 550000,
    '["Home Loan", "Personal Loan", "Business Loan", "Vehicle Loan"]'::jsonb,
    'Full Banking Services', 2003, 'Mumbai, Maharashtra', 'Maharashtra',
    85000, 1, 30000, false, 'https://www.kotak.com'
  ),
  
  -- Cooperative Banks
  (
    'Saraswat Co-operative Bank', 'Cooperative Bank', 6500,
    '["Home Loan", "Personal Loan", "Business Loan"]'::jsonb,
    'Retail Banking', 1918, 'Mumbai, Maharashtra', 'Maharashtra',
    3500, 0.5, 500, false, 'https://www.saraswatbank.com'
  ),
  (
    'TJSB Sahakari Bank', 'Cooperative Bank', 4200,
    '["Home Loan", "Personal Loan", "MSME Loan"]'::jsonb,
    'MSME Banking', 1975, 'Mumbai, Maharashtra', 'Maharashtra',
    2800, 0.5, 200, false, 'https://www.tjsb.in'
  ),
  
  -- More NBFCs
  (
    'LIC Housing Finance', 'NBFC', 275000,
    '["Home Loan", "Loan Against Property"]'::jsonb,
    'Home Loan', 1989, 'Mumbai, Maharashtra', 'Maharashtra',
    5200, 5, 10000, false, 'https://www.lichousing.com'
  );