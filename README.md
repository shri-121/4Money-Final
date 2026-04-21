# 4Money — Complete Deployment Guide
> Follow these steps exactly. Everything is free. No credit card needed.

---

## 📁 Project Structure
```
4money/
├── backend/          ← Python FastAPI server
│   ├── server.py     ← Main backend (all API routes)
│   ├── requirements.txt
│   └── .env.example  ← Copy to .env and fill values
├── frontend/         ← React app
│   ├── src/
│   │   ├── App.js
│   │   ├── api.js
│   │   ├── index.js
│   │   ├── index.css
│   │   └── pages/
│   │       ├── Register.jsx
│   │       ├── Login.jsx
│   │       ├── Dashboard.jsx
│   │       ├── AdminLogin.jsx
│   │       └── AdminDashboard.jsx
│   ├── public/index.html
│   ├── package.json
│   └── .env.example
└── README.md
```

---

## STEP 1 — MongoDB Atlas (Free Database)

1. Go to https://mongodb.com/atlas
2. Click "Try Free" → Sign up
3. Choose "Free" tier (M0) → Select region → Create
4. Security → Database Access → Add user → Set username + password (save these!)
5. Security → Network Access → Add IP Address → "Allow access from anywhere" (0.0.0.0/0)
6. Deployment → Database → Connect → Drivers → Copy the connection string
   - It looks like: `mongodb+srv://USERNAME:PASSWORD@cluster0.xxxxx.mongodb.net/`
   - Replace `<password>` with your actual password
7. **Save this URL** — you'll need it soon

---

## STEP 2 — Gmail App Password (For OTP Emails)

1. Go to your Google Account: https://myaccount.google.com
2. Security → 2-Step Verification → Turn it ON (required)
3. Security → Search "App passwords" → Select app: Mail → Generate
4. Copy the 16-character password (like: `abcd efgh ijkl mnop`)
5. **Save this password**

---

## STEP 3 — GitHub (Put your code online)

1. Go to https://github.com → Sign up (free)
2. Click "+" → New repository
3. Name: `4money-app` → Public → Create repository
4. Download GitHub Desktop from https://desktop.github.com (easiest way)
5. Open GitHub Desktop → File → Clone Repository → paste your repo URL
6. Copy ALL files from this zip into the cloned folder
7. In GitHub Desktop → Commit to main → Push origin

---

## STEP 4 — Deploy Backend on Render (Free)

1. Go to https://render.com → Sign up with GitHub
2. New → Web Service → Connect your `4money-app` repo
3. Settings:
   - **Name**: 4money-backend
   - **Root Directory**: `backend`
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free
4. Add Environment Variables (click "Add Environment Variable" for each):

   | Key | Value |
   |-----|-------|
   | MONGO_URL | your mongodb connection string |
   | DB_NAME | fourmoney_db |
   | JWT_SECRET | any-random-long-string-here |
   | GMAIL_USER | your-gmail@gmail.com |
   | GMAIL_APP_PASSWORD | your 16-char app password |
   | PLATFORM_WALLET_ADDRESS | your TRC20 USDT wallet address |
   | USDT_TO_SC_RATE | 106.4 |
   | MIN_DEPOSIT_USDT | 5 |
   | MIN_WITHDRAW_SC | 1000 |
   | ADMIN_USERNAME | admin |
   | ADMIN_PASSWORD | YourSecretAdminPassword123 |

5. Click **Deploy Web Service**
6. Wait ~3 minutes. You'll get a URL like: `https://4money-backend.onrender.com`
7. Test it: open `https://4money-backend.onrender.com/api/health` — should show `{"status":"ok"}`
8. **Save this URL**

---

## STEP 5 — Deploy Frontend on Vercel (Free)

1. Go to https://vercel.com → Sign up with GitHub
2. New Project → Import `4money-app` repo
3. Settings:
   - **Framework Preset**: Create React App
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `build`
4. Add Environment Variable:

   | Key | Value |
   |-----|-------|
   | REACT_APP_BACKEND_URL | https://4money-backend.onrender.com |

   *(Use YOUR Render URL from Step 4)*

5. Click **Deploy**
6. Wait ~2 minutes. You'll get a URL like: `https://4money-app.vercel.app`
7. **Your app is LIVE!** 🎉

---

## STEP 6 — Access Admin Panel

- URL: `https://4money-app.vercel.app/admin/login`
- Username: `admin` (or whatever you set in ADMIN_USERNAME)
- Password: whatever you set in ADMIN_PASSWORD

---

## How The App Works

### User Flow:
1. User registers with email → gets OTP → creates account
2. User deposits USDT → enters amount → gets OTP to confirm → sends USDT to your wallet → uploads screenshot
3. **You (admin) approve the deposit** → SCoins credited to user automatically
4. User can withdraw SCoins as INR → request goes to you (admin)
5. **You (admin) manually send money via UPI** → mark as approved in panel

### SCoins System:
- 1 USDT = 106.4 SCoins
- 1 SCoin = ₹1 INR
- Minimum deposit: 5 USDT
- Minimum withdrawal: 1,000 SCoins

### Referral System:
- Each user has a unique invite code
- When someone registers with that code → both get +106.4 SCoins bonus

---

## Common Issues & Fixes

**Backend not starting on Render:**
- Check that Root Directory is set to `backend`
- Check all environment variables are filled correctly

**"Cannot connect to server" on frontend:**
- Make sure REACT_APP_BACKEND_URL has NO trailing slash
- Make sure it starts with `https://`

**OTP emails not sending:**
- Check GMAIL_USER and GMAIL_APP_PASSWORD are correct
- Make sure 2-Step Verification is ON in your Google account
- App will still work — it shows OTP on screen in dev mode

**Admin can't login:**
- Wait 30 seconds after backend starts — admin is auto-created on startup
- Check ADMIN_USERNAME and ADMIN_PASSWORD env variables on Render

**Render backend goes to sleep (free plan):**
- Free Render services sleep after 15 minutes of inactivity
- First request after sleep takes ~30 seconds
- To fix: upgrade to Render Starter ($7/month) OR use https://cron-job.org to ping your URL every 10 minutes (free)

---

## Keeping Backend Awake (Free Trick)

1. Go to https://cron-job.org → Sign up free
2. New Cronjob:
   - URL: `https://your-backend.onrender.com/api/health`
   - Schedule: Every 10 minutes
3. This prevents Render from sleeping your backend

---

*4Money App — Built with FastAPI + React + MongoDB*
