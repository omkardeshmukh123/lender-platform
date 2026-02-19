# 🚀 DEPLOYMENT GUIDE - Lender Discovery Platform

## Complete deployment in 30 minutes - LIVE TODAY

---

## STEP 1: SUPABASE SETUP (10 min)

### 1.1 Create Project
1. Go to https://supabase.com
2. Click "Start your project"
3. Sign in with GitHub (free tier)
4. Click "New Project"
   - Name: `lender-discovery`
   - Database Password: (save this)
   - Region: Asia South (Mumbai) or closest
   - Click "Create new project" (takes 2 min)

### 1.2 Create Database
1. In Supabase dashboard, click "SQL Editor" (left sidebar)
2. Click "New query"
3. Copy the ENTIRE contents of `../database/schema.sql`
4. Paste into SQL editor
5. Click "Run" (green button)
6. You should see: "Success. 15 rows affected" (sample data inserted)

### 1.3 Get API Keys
1. Click "Project Settings" (gear icon, bottom left)
2. Click "API" in sidebar
3. Copy these two values:
   - Project URL (looks like: `https://xxxxx.supabase.co`)
   - `anon` `public` key (long string starting with `eyJ...`)

---

## STEP 2: FRONTEND SETUP (5 min)

### 2.1 Install Dependencies
```bash
cd frontend
npm install
```

### 2.2 Configure Environment
```bash
# Copy template
cp .env.local.template .env.local

# Edit .env.local and add your Supabase credentials:
NEXT_PUBLIC_SUPABASE_URL=https://xxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJhbGc...your_key_here
```

### 2.3 Test Locally
```bash
npm run dev
```

Open http://localhost:3000
- You should see the platform with 15 sample lenders
- Test the filters
- Click "Visit Website" buttons

---

## STEP 3: DEPLOY TO VERCEL (5 min)

### 3.1 Push to GitHub
```bash
# In frontend directory
git init
git add .
git commit -m "Initial commit - Lender Discovery Platform"

# Create new repo on GitHub: lender-discovery-platform
# Then:
git remote add origin https://github.com/YOUR_USERNAME/lender-discovery-platform.git
git branch -M main
git push -u origin main
```

### 3.2 Deploy on Vercel
1. Go to https://vercel.com
2. Sign in with GitHub
3. Click "Add New" → "Project"
4. Import your `lender-discovery-platform` repo
5. Configure:
   - Framework Preset: Next.js (auto-detected)
   - Root Directory: `frontend`
   - Click "Environment Variables"
   - Add both:
     - `NEXT_PUBLIC_SUPABASE_URL` = your Supabase URL
     - `NEXT_PUBLIC_SUPABASE_ANON_KEY` = your anon key
6. Click "Deploy"
7. Wait 2 minutes
8. Done! You get a live URL like: `lender-discovery-platform.vercel.app`

---

## STEP 4: ADD MORE DATA (Ongoing)

Your platform is LIVE with 15 sample companies. To add more:

### Option A: Manual CSV Import
1. Create CSV with columns matching `schema.sql`
2. In Supabase dashboard → Table Editor → `lenders`
3. Click "Insert" → "Insert rows from CSV"
4. Upload your CSV

### Option B: Run Extraction Script
```bash
# When you have Gemini API key working:
cd ../backend
export GEMINI_API_KEY="your_key"
python run_extraction.py

# This outputs: data/output/extracted_lenders.csv
# Import to Supabase using Option A
```

### Option C: Batch SQL Insert
1. Write SQL INSERT statements
2. Run in Supabase SQL Editor
3. Data appears immediately

---

## 🎉 SUCCESS CHECKLIST

- [ ] Supabase project created
- [ ] Database schema deployed (15 sample lenders)
- [ ] Frontend running locally
- [ ] Filters working (loan type, state, ticket size, company type)
- [ ] Deployed to Vercel
- [ ] Live URL working
- [ ] Share URL with sir

---

## WHAT SIR WILL SEE

Live demo at: `https://your-app.vercel.app`

**Features working TODAY:**
✅ Filter by Loan Type (MSME, Home, Personal, Gold, Vehicle, etc.)
✅ Filter by State (Maharashtra, Kerala, Delhi, etc.)
✅ Filter by Ticket Size (Micro, Small, Medium, Large)
✅ Filter by Company Type (NBFC, PSU Bank, Private Bank, Cooperative)
✅ Company cards showing all 8 required fields
✅ "Visit Website" buttons
✅ Real-time filtering (no page reload)
✅ Responsive design (works on mobile)

**Sample data included:**
- 5 NBFCs (121 Finance, 4Fin, Bajaj, Muthoot, Shriram)
- 3 PSU Banks (SBI, PNB, Bank of Baroda)
- 3 Private Banks (HDFC, ICICI, Axis)
- 2 Cooperative Banks (Saraswat, TJSB)
- 2 More NBFCs (LIC Housing, Mahindra Finance)

Total: 15 lenders covering all 5 categories

---

## FUTURE ENHANCEMENTS

Once deployed, you can add:
1. **Search bar** - Search by company name
2. **Export to CSV** - Download filtered results
3. **Company detail page** - Click card → full details
4. **Chatbot** - AI assistant for loan recommendations
5. **User accounts** - Save favorite lenders
6. **Contact forms** - Direct inquiry to lenders

But for MVP - current version is PERFECT ✅

---

## TROUBLESHOOTING

### "Error fetching lenders"
- Check Supabase URL and anon key in `.env.local`
- Verify RLS policy exists (run schema.sql again)

### "No lenders showing"
- Check Supabase Table Editor → `lenders` table has data
- Run INSERT statements from schema.sql again

### "Vercel build fails"
- Make sure Root Directory is set to `frontend`
- Check environment variables are added in Vercel

### Need help?
- Supabase docs: https://supabase.com/docs
- Vercel docs: https://vercel.com/docs
- Next.js docs: https://nextjs.org/docs

---

## 📞 SHOW SIR

Once deployed, show him:
1. The live URL
2. Filter by "MSME Loan" → see only MSME lenders
3. Filter by "Maharashtra" → see only Maharashtra HQs
4. Filter by "Small" ticket size → see lenders for ₹5-50L range
5. Filter by "NBFC" type → see only NBFCs

**He'll see a working product in 30 minutes! 🎉**
