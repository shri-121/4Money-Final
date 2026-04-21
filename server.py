from fastapi import FastAPI, APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os, logging, random, uuid, bcrypt, jwt, aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# ── Database ──────────────────────────────────────────────────────────────────
MONGO_URL = os.environ.get('MONGO_URL', '')
DB_NAME   = os.environ.get('DB_NAME', 'fourmoney_db')
client    = AsyncIOMotorClient(MONGO_URL)
db        = client[DB_NAME]

# ── Config ────────────────────────────────────────────────────────────────────
JWT_SECRET             = os.environ.get('JWT_SECRET', '4money-super-secret-change-me')
JWT_ALGORITHM          = 'HS256'
JWT_EXPIRY_HOURS       = 24 * 7

GMAIL_USER             = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASSWORD     = os.environ.get('GMAIL_APP_PASSWORD', '')

PLATFORM_WALLET        = os.environ.get('PLATFORM_WALLET_ADDRESS', 'TXXXXxxxxYYYYyyyyZZZZzzzz1234567890')
USDT_TO_SC_RATE        = float(os.environ.get('USDT_TO_SC_RATE', '106.4'))
MIN_DEPOSIT_USDT       = float(os.environ.get('MIN_DEPOSIT_USDT', '5'))
MIN_WITHDRAW_SC        = float(os.environ.get('MIN_WITHDRAW_SC', '1000'))

FRONTEND_URL           = os.environ.get('FRONTEND_URL', '*')

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="4Money API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
router = APIRouter(prefix="/api")
security = HTTPBearer()
logger   = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Models ────────────────────────────────────────────────────────────────────
class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id:               str   = Field(default_factory=lambda: str(uuid.uuid4()))
    email:            EmailStr
    password_hash:    str
    scoins:           float = 0.0
    usdt_deposited:   float = 0.0
    invite_code:      str   = Field(default_factory=lambda: str(uuid.uuid4())[:8].upper())
    referred_by:      Optional[str] = None
    referral_bonus:   float = 0.0
    upi_id:           Optional[str] = None
    phone:            Optional[str] = None
    is_email_verified: bool = False
    is_banned:        bool  = False
    created_at:       str   = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class Deposit(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id:               str   = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id:          str
    user_email:       str
    usdt_amount:      float
    sc_amount:        float
    wallet_address:   str
    transaction_id:   Optional[str] = None
    screenshot_base64: Optional[str] = None
    status:           str   = "pending"   # pending | approved | rejected
    admin_notes:      Optional[str] = None
    otp_verified:     bool  = False
    created_at:       str   = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    approved_at:      Optional[str] = None

class Withdrawal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id:               str   = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id:          str
    user_email:       str
    sc_amount:        float
    inr_amount:       float
    upi_id:           str
    status:           str   = "pending"
    admin_notes:      Optional[str] = None
    created_at:       str   = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class OTPRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email:      EmailStr
    otp_code:   str
    verified:   bool   = False
    expires_at: str
    purpose:    str
    created_at: str    = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class Admin(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id:            str  = Field(default_factory=lambda: str(uuid.uuid4()))
    username:      str
    password_hash: str
    role:          str  = "admin"
    created_at:    str  = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# ── Request schemas ───────────────────────────────────────────────────────────
class SendOTPReq(BaseModel):
    email: EmailStr
    purpose: str = "registration"

class VerifyOTPReq(BaseModel):
    email: EmailStr
    otp_code: str
    purpose: str = "registration"

class RegisterReq(BaseModel):
    email: EmailStr
    password: str
    invite_code: Optional[str] = None

class LoginReq(BaseModel):
    email: EmailStr
    password: str

class UpdateUPIReq(BaseModel):
    upi_id: str
    phone: str

class InitDepositReq(BaseModel):
    usdt_amount: float

class VerifyDepositOTPReq(BaseModel):
    deposit_id: str
    otp_code: str

class SubmitProofReq(BaseModel):
    deposit_id: str
    transaction_id: str
    screenshot_base64: str

class WithdrawReq(BaseModel):
    sc_amount: float

class AdminLoginReq(BaseModel):
    username: str
    password: str

class ApproveDepositReq(BaseModel):
    deposit_id: str
    admin_notes: Optional[str] = None

class RejectDepositReq(BaseModel):
    deposit_id: str
    admin_notes: str

class ApproveWithdrawReq(BaseModel):
    withdrawal_id: str
    admin_notes: Optional[str] = None

class RejectWithdrawReq(BaseModel):
    withdrawal_id: str
    admin_notes: str

class AdjustBalanceReq(BaseModel):
    user_id: str
    amount: float
    note: Optional[str] = "Admin adjustment"

class BanUserReq(BaseModel):
    user_id: str
    ban: bool

# ── Helpers ───────────────────────────────────────────────────────────────────
def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()

def check_pw(p: str, h: str) -> bool:
    return bcrypt.checkpw(p.encode(), h.encode())

def make_token(uid: str, role: str = "user") -> str:
    return jwt.encode(
        {"user_id": uid, "role": role,
         "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS)},
        JWT_SECRET, algorithm=JWT_ALGORITHM
    )

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Session expired. Please login again.")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token.")

async def current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    data = decode_token(creds.credentials)
    if data.get("role") != "user":
        raise HTTPException(403, "Not authorized")
    u = await db.users.find_one({"id": data["user_id"]}, {"_id": 0})
    if not u:
        raise HTTPException(404, "User not found")
    if u.get("is_banned"):
        raise HTTPException(403, "Your account has been suspended.")
    return u

async def current_admin(creds: HTTPAuthorizationCredentials = Depends(security)):
    data = decode_token(creds.credentials)
    if data.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    a = await db.admins.find_one({"id": data["user_id"]}, {"_id": 0})
    if not a:
        raise HTTPException(404, "Admin not found")
    return a

def fmt_user(u: dict) -> dict:
    return {
        "id":               u["id"],
        "email":            u["email"],
        "scoins":           u.get("scoins", 0),
        "usdt_deposited":   u.get("usdt_deposited", 0),
        "invite_code":      u["invite_code"],
        "upi_id":           u.get("upi_id"),
        "phone":            u.get("phone"),
        "is_email_verified": u.get("is_email_verified", False),
        "referral_bonus":   u.get("referral_bonus", 0),
    }

async def send_otp_email(email: str, otp: str, purpose: str) -> bool:
    subject_map = {
        "registration": "4Money — Email Verification Code",
        "deposit":      "4Money — Deposit OTP",
        "withdrawal":   "4Money — Withdrawal OTP",
        "login":        "4Money — Login OTP",
    }
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.info(f"[MOCK OTP] {email} → {otp} ({purpose})")
        return True
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject_map.get(purpose, "4Money — Verification Code")
        msg["From"]    = GMAIL_USER
        msg["To"]      = email
        html = f"""
        <html><body style="margin:0;padding:0;background:#0f0f0f;font-family:'Helvetica Neue',Arial,sans-serif">
          <div style="max-width:480px;margin:40px auto;background:#1a1a1a;border-radius:20px;overflow:hidden;border:1px solid #333">
            <div style="background:linear-gradient(135deg,#E8590C,#ff8c42);padding:32px;text-align:center">
              <h1 style="color:#fff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.5px">4Money</h1>
              <p style="color:rgba(255,255,255,0.8);margin:8px 0 0;font-size:14px">Crypto Wealth Platform</p>
            </div>
            <div style="padding:40px 32px">
              <p style="color:#ccc;font-size:15px;margin:0 0 24px">Your verification code for <strong style="color:#fff">{purpose}</strong> is:</p>
              <div style="background:#111;border:2px solid #E8590C;border-radius:16px;padding:28px;text-align:center;margin:0 0 24px">
                <span style="color:#E8590C;font-size:44px;font-weight:800;letter-spacing:10px">{otp}</span>
              </div>
              <p style="color:#666;font-size:13px;margin:0">This code expires in <strong style="color:#aaa">10 minutes</strong>. Never share this code with anyone.</p>
            </div>
            <div style="padding:20px 32px;border-top:1px solid #222;text-align:center">
              <p style="color:#555;font-size:12px;margin:0">© 2024 4Money · Secure Crypto Platform</p>
            </div>
          </div>
        </body></html>
        """
        msg.attach(MIMEText(html, "html"))
        await aiosmtplib.send(
            msg, hostname="smtp.gmail.com", port=587,
            start_tls=True, username=GMAIL_USER, password=GMAIL_APP_PASSWORD
        )
        logger.info(f"Email sent → {email}")
        return True
    except Exception as e:
        logger.error(f"Email failed: {e}")
        return False

async def store_otp(email: str, purpose: str) -> str:
    otp = str(random.randint(100000, 999999))
    await db.otp_records.delete_many({"email": email, "purpose": purpose})
    doc = OTPRecord(
        email=email, otp_code=otp,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        purpose=purpose
    ).model_dump()
    await db.otp_records.insert_one(doc)
    return otp

async def check_otp(email: str, otp: str, purpose: str) -> bool:
    rec = await db.otp_records.find_one(
        {"email": email, "otp_code": otp, "verified": False, "purpose": purpose}, {"_id": 0}
    )
    if not rec:
        return False
    if datetime.now(timezone.utc) > datetime.fromisoformat(rec["expires_at"]):
        return False
    await db.otp_records.update_one(
        {"email": email, "otp_code": otp, "purpose": purpose},
        {"$set": {"verified": True}}
    )
    return True

# ── Startup: create admin if missing ──────────────────────────────────────────
@app.on_event("startup")
async def startup():
    admin_user = os.environ.get("ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("ADMIN_PASSWORD", "Admin@4money2024")
    existing   = await db.admins.find_one({"username": admin_user})
    if not existing:
        adm = Admin(username=admin_user, password_hash=hash_pw(admin_pass))
        await db.admins.insert_one(adm.model_dump())
        logger.info(f"Admin created: {admin_user}")

# ══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health")
async def health():
    return {"status": "ok", "message": "4Money API is running"}

@router.post("/auth/send-otp")
async def send_otp(req: SendOTPReq):
    if req.purpose == "registration":
        if await db.users.find_one({"email": req.email}):
            raise HTTPException(400, "Email already registered.")
    otp = await store_otp(req.email, req.purpose)
    await send_otp_email(req.email, otp, req.purpose)
    resp = {"status": "sent", "message": f"Code sent to {req.email}"}
    if not GMAIL_USER:
        resp["dev_otp"] = otp   # only shown when SMTP not configured
    return resp

@router.post("/auth/verify-otp")
async def verify_otp(req: VerifyOTPReq):
    ok = await check_otp(req.email, req.otp_code, req.purpose)
    if not ok:
        raise HTTPException(400, "Invalid or expired code.")
    return {"valid": True, "message": "Verified!"}

@router.post("/auth/register")
async def register(req: RegisterReq):
    if await db.users.find_one({"email": req.email}):
        raise HTTPException(400, "Email already registered.")
    if not await db.otp_records.find_one({"email": req.email, "verified": True, "purpose": "registration"}):
        raise HTTPException(400, "Please verify your email first.")
    if len(req.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    referred_by = None
    if req.invite_code:
        ref = await db.users.find_one({"invite_code": req.invite_code.upper()}, {"_id": 0})
        if ref:
            referred_by = ref["id"]
    user = User(email=req.email, password_hash=hash_pw(req.password),
                referred_by=referred_by, is_email_verified=True)
    await db.users.insert_one(user.model_dump())
    # Give referral bonus
    if referred_by:
        bonus = USDT_TO_SC_RATE  # 1 USDT worth of SCoins as bonus
        await db.users.update_one({"id": referred_by}, {"$inc": {"scoins": bonus, "referral_bonus": bonus}})
        await db.users.update_one({"id": user.id}, {"$inc": {"scoins": bonus}})
        await db.transactions.insert_many([
            {"user_id": referred_by, "type": "referral_bonus", "amount": bonus, "label": f"Referral bonus for {req.email}", "created_at": datetime.now(timezone.utc).isoformat()},
            {"user_id": user.id, "type": "welcome_bonus", "amount": bonus, "label": "Welcome bonus", "created_at": datetime.now(timezone.utc).isoformat()},
        ])
    token = make_token(user.id, "user")
    return {"message": "Account created!", "token": token, "user": fmt_user(user.model_dump())}

@router.post("/auth/login")
async def login(req: LoginReq):
    u = await db.users.find_one({"email": req.email}, {"_id": 0})
    if not u or not check_pw(req.password, u["password_hash"]):
        raise HTTPException(401, "Invalid email or password.")
    if u.get("is_banned"):
        raise HTTPException(403, "Your account has been suspended.")
    return {"message": "Login successful", "token": make_token(u["id"], "user"), "user": fmt_user(u)}

@router.get("/auth/me")
async def get_me(u: dict = Depends(current_user)):
    fresh = await db.users.find_one({"id": u["id"]}, {"_id": 0})
    return fmt_user(fresh)

# ══════════════════════════════════════════════════════════════════════════════
# USER ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/user/update-upi")
async def update_upi(req: UpdateUPIReq, u: dict = Depends(current_user)):
    phone = req.phone.strip().replace(" ", "").replace("-", "")
    if not phone.isdigit() or len(phone) < 10:
        raise HTTPException(400, "Enter valid 10-digit phone number.")
    await db.users.update_one({"id": u["id"]}, {"$set": {"upi_id": req.upi_id.strip(), "phone": phone}})
    return {"message": "UPI ID saved successfully!"}

@router.get("/user/transactions")
async def get_transactions(u: dict = Depends(current_user)):
    deposits = await db.deposits.find({"user_id": u["id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    withdrawals = await db.withdrawals.find({"user_id": u["id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    all_tx = [{"kind": "deposit", **d} for d in deposits] + [{"kind": "withdrawal", **w} for w in withdrawals]
    all_tx.sort(key=lambda x: x["created_at"], reverse=True)
    return {"transactions": all_tx}

@router.get("/user/balance")
async def get_balance(u: dict = Depends(current_user)):
    fresh = await db.users.find_one({"id": u["id"]}, {"_id": 0})
    return {"scoins": fresh.get("scoins", 0), "usdt_deposited": fresh.get("usdt_deposited", 0)}

# ══════════════════════════════════════════════════════════════════════════════
# DEPOSIT ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/deposit/initiate")
async def initiate_deposit(req: InitDepositReq, u: dict = Depends(current_user)):
    if req.usdt_amount < MIN_DEPOSIT_USDT:
        raise HTTPException(400, f"Minimum deposit is {MIN_DEPOSIT_USDT} USDT.")
    sc_amount = round(req.usdt_amount * USDT_TO_SC_RATE, 2)
    dep = Deposit(
        user_id=u["id"], user_email=u["email"],
        usdt_amount=req.usdt_amount, sc_amount=sc_amount,
        wallet_address=PLATFORM_WALLET
    )
    await db.deposits.insert_one(dep.model_dump())
    otp = await store_otp(u["email"], "deposit")
    await send_otp_email(u["email"], otp, "deposit")
    resp = {
        "deposit_id": dep.id, "usdt_amount": req.usdt_amount,
        "sc_amount": sc_amount, "rate": USDT_TO_SC_RATE,
        "message": "OTP sent to your email to confirm deposit."
    }
    if not GMAIL_USER:
        resp["dev_otp"] = otp
    return resp

@router.post("/deposit/verify-otp")
async def verify_deposit_otp(req: VerifyDepositOTPReq, u: dict = Depends(current_user)):
    dep = await db.deposits.find_one({"id": req.deposit_id, "user_id": u["id"]}, {"_id": 0})
    if not dep:
        raise HTTPException(404, "Deposit not found.")
    if dep["otp_verified"]:
        return {"verified": True, "wallet_address": dep["wallet_address"],
                "usdt_amount": dep["usdt_amount"], "sc_amount": dep["sc_amount"]}
    if not await check_otp(u["email"], req.otp_code, "deposit"):
        raise HTTPException(400, "Invalid or expired OTP.")
    await db.deposits.update_one({"id": req.deposit_id}, {"$set": {"otp_verified": True}})
    return {"verified": True, "wallet_address": dep["wallet_address"],
            "usdt_amount": dep["usdt_amount"], "sc_amount": dep["sc_amount"]}

@router.post("/deposit/submit-proof")
async def submit_proof(req: SubmitProofReq, u: dict = Depends(current_user)):
    dep = await db.deposits.find_one({"id": req.deposit_id, "user_id": u["id"]}, {"_id": 0})
    if not dep:
        raise HTTPException(404, "Deposit not found.")
    if not dep["otp_verified"]:
        raise HTTPException(400, "Please verify OTP first.")
    await db.deposits.update_one(
        {"id": req.deposit_id},
        {"$set": {"transaction_id": req.transaction_id,
                  "screenshot_base64": req.screenshot_base64,
                  "status": "pending"}}
    )
    return {"message": "Proof submitted! Admin will review within 30 minutes.", "status": "pending"}

@router.get("/deposit/status/{deposit_id}")
async def deposit_status(deposit_id: str, u: dict = Depends(current_user)):
    dep = await db.deposits.find_one({"id": deposit_id, "user_id": u["id"]}, {"_id": 0})
    if not dep:
        raise HTTPException(404, "Not found.")
    return {k: dep[k] for k in ["id", "usdt_amount", "sc_amount", "status", "created_at", "admin_notes"] if k in dep}

# ══════════════════════════════════════════════════════════════════════════════
# WITHDRAWAL ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/withdraw/request")
async def request_withdrawal(req: WithdrawReq, u: dict = Depends(current_user)):
    if req.sc_amount < MIN_WITHDRAW_SC:
        raise HTTPException(400, f"Minimum withdrawal is {MIN_WITHDRAW_SC} SCoins.")
    fresh = await db.users.find_one({"id": u["id"]}, {"_id": 0})
    if fresh.get("scoins", 0) < req.sc_amount:
        raise HTTPException(400, "Insufficient SCoins balance.")
    if not fresh.get("upi_id"):
        raise HTTPException(400, "Please add your UPI ID first.")
    # Lock SCoins immediately
    await db.users.update_one({"id": u["id"]}, {"$inc": {"scoins": -req.sc_amount}})
    wd = Withdrawal(
        user_id=u["id"], user_email=u["email"],
        sc_amount=req.sc_amount, inr_amount=req.sc_amount,
        upi_id=fresh["upi_id"]
    )
    await db.withdrawals.insert_one(wd.model_dump())
    return {"message": "Withdrawal request submitted! Will be processed within 24 hours.",
            "withdrawal_id": wd.id, "sc_amount": req.sc_amount, "inr_amount": req.sc_amount}

# ══════════════════════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/login")
async def admin_login(req: AdminLoginReq):
    a = await db.admins.find_one({"username": req.username}, {"_id": 0})
    if not a or not check_pw(req.password, a["password_hash"]):
        raise HTTPException(401, "Invalid credentials.")
    return {"message": "Login successful",
            "token": make_token(a["id"], "admin"),
            "admin": {"id": a["id"], "username": a["username"]}}

@router.get("/admin/stats")
async def admin_stats(a: dict = Depends(current_admin)):
    total_users      = await db.users.count_documents({})
    pending_deposits = await db.deposits.count_documents({"status": "pending"})
    approved_deposits= await db.deposits.count_documents({"status": "approved"})
    pending_withdrawals = await db.withdrawals.count_documents({"status": "pending"})
    total_sc_pipeline = [{"$group": {"_id": None, "total": {"$sum": "$scoins"}}}]
    sc_result = await db.users.aggregate(total_sc_pipeline).to_list(1)
    total_scoins = sc_result[0]["total"] if sc_result else 0
    usdt_pipeline = [{"$match": {"status": "approved"}}, {"$group": {"_id": None, "total": {"$sum": "$usdt_amount"}}}]
    usdt_result = await db.deposits.aggregate(usdt_pipeline).to_list(1)
    total_usdt = usdt_result[0]["total"] if usdt_result else 0
    return {
        "total_users": total_users,
        "pending_deposits": pending_deposits,
        "approved_deposits": approved_deposits,
        "pending_withdrawals": pending_withdrawals,
        "total_scoins_issued": round(total_scoins, 2),
        "total_usdt_received": round(total_usdt, 2),
    }

@router.get("/admin/users")
async def admin_get_users(a: dict = Depends(current_admin)):
    users = await db.users.find({}, {"_id": 0, "password_hash": 0}).sort("created_at", -1).to_list(5000)
    return {"users": users, "total": len(users)}

@router.get("/admin/deposits")
async def admin_get_deposits(status: Optional[str] = None, a: dict = Depends(current_admin)):
    query = {}
    if status:
        query["status"] = status
    deps = await db.deposits.find(query, {"_id": 0}).sort("created_at", -1).to_list(5000)
    return {"deposits": deps, "total": len(deps)}

@router.get("/admin/withdrawals")
async def admin_get_withdrawals(status: Optional[str] = None, a: dict = Depends(current_admin)):
    query = {}
    if status:
        query["status"] = status
    wds = await db.withdrawals.find(query, {"_id": 0}).sort("created_at", -1).to_list(5000)
    return {"withdrawals": wds, "total": len(wds)}

@router.post("/admin/deposit/approve")
async def approve_deposit(req: ApproveDepositReq, a: dict = Depends(current_admin)):
    dep = await db.deposits.find_one({"id": req.deposit_id}, {"_id": 0})
    if not dep:
        raise HTTPException(404, "Deposit not found.")
    if dep["status"] == "approved":
        raise HTTPException(400, "Already approved.")
    await db.deposits.update_one(
        {"id": req.deposit_id},
        {"$set": {"status": "approved", "admin_notes": req.admin_notes,
                  "approved_at": datetime.now(timezone.utc).isoformat()}}
    )
    await db.users.update_one(
        {"id": dep["user_id"]},
        {"$inc": {"scoins": dep["sc_amount"], "usdt_deposited": dep["usdt_amount"]}}
    )
    await db.transactions.insert_one({
        "user_id": dep["user_id"], "type": "deposit_approved",
        "amount": dep["sc_amount"], "label": f"USDT Deposit {dep['usdt_amount']} USDT approved",
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    # Notify user via email
    user = await db.users.find_one({"id": dep["user_id"]}, {"_id": 0})
    if user:
        if GMAIL_USER:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = "4Money — Deposit Approved! 🎉"
                msg["From"] = GMAIL_USER
                msg["To"] = user["email"]
                html = f"""<html><body style="background:#0f0f0f;font-family:Arial,sans-serif">
                <div style="max-width:480px;margin:40px auto;background:#1a1a1a;border-radius:20px;padding:40px;border:1px solid #333">
                <h2 style="color:#E8590C">Deposit Approved! ✅</h2>
                <p style="color:#ccc">Your deposit of <strong style="color:#fff">{dep['usdt_amount']} USDT</strong> has been approved.</p>
                <p style="color:#ccc"><strong style="color:#E8590C">{dep['sc_amount']} SCoins</strong> have been added to your account.</p>
                </div></body></html>"""
                msg.attach(MIMEText(html, "html"))
                await aiosmtplib.send(msg, hostname="smtp.gmail.com", port=587,
                    start_tls=True, username=GMAIL_USER, password=GMAIL_APP_PASSWORD)
            except Exception as e:
                logger.error(f"Notify email failed: {e}")
    return {"message": f"Deposit approved. {dep['sc_amount']} SCoins credited to user."}

@router.post("/admin/deposit/reject")
async def reject_deposit(req: RejectDepositReq, a: dict = Depends(current_admin)):
    dep = await db.deposits.find_one({"id": req.deposit_id}, {"_id": 0})
    if not dep:
        raise HTTPException(404, "Not found.")
    await db.deposits.update_one(
        {"id": req.deposit_id},
        {"$set": {"status": "rejected", "admin_notes": req.admin_notes}}
    )
    return {"message": "Deposit rejected."}

@router.post("/admin/withdrawal/approve")
async def approve_withdrawal(req: ApproveWithdrawReq, a: dict = Depends(current_admin)):
    wd = await db.withdrawals.find_one({"id": req.withdrawal_id}, {"_id": 0})
    if not wd:
        raise HTTPException(404, "Not found.")
    if wd["status"] == "approved":
        raise HTTPException(400, "Already approved.")
    await db.withdrawals.update_one(
        {"id": req.withdrawal_id},
        {"$set": {"status": "approved", "admin_notes": req.admin_notes}}
    )
    return {"message": f"Withdrawal approved. Send ₹{wd['inr_amount']} to {wd['upi_id']}"}

@router.post("/admin/withdrawal/reject")
async def reject_withdrawal(req: RejectWithdrawReq, a: dict = Depends(current_admin)):
    wd = await db.withdrawals.find_one({"id": req.withdrawal_id}, {"_id": 0})
    if not wd:
        raise HTTPException(404, "Not found.")
    # Refund SCoins
    await db.users.update_one({"id": wd["user_id"]}, {"$inc": {"scoins": wd["sc_amount"]}})
    await db.withdrawals.update_one(
        {"id": req.withdrawal_id},
        {"$set": {"status": "rejected", "admin_notes": req.admin_notes}}
    )
    return {"message": "Withdrawal rejected. SCoins refunded to user."}

@router.post("/admin/adjust-balance")
async def adjust_balance(req: AdjustBalanceReq, a: dict = Depends(current_admin)):
    u = await db.users.find_one({"id": req.user_id}, {"_id": 0})
    if not u:
        raise HTTPException(404, "User not found.")
    await db.users.update_one({"id": req.user_id}, {"$inc": {"scoins": req.amount}})
    await db.transactions.insert_one({
        "user_id": req.user_id, "type": "admin_adjustment",
        "amount": req.amount, "label": req.note,
        "created_at": datetime.now(timezone.utc).isoformat()
    })
    return {"message": f"Balance adjusted by {req.amount} SCoins for {u['email']}"}

@router.post("/admin/ban-user")
async def ban_user(req: BanUserReq, a: dict = Depends(current_admin)):
    u = await db.users.find_one({"id": req.user_id}, {"_id": 0})
    if not u:
        raise HTTPException(404, "User not found.")
    await db.users.update_one({"id": req.user_id}, {"$set": {"is_banned": req.ban}})
    action = "banned" if req.ban else "unbanned"
    return {"message": f"User {u['email']} has been {action}."}

# ── Mount router ──────────────────────────────────────────────────────────────
app.include_router(router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=True)
