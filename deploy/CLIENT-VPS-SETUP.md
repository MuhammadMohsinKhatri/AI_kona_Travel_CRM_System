# Setting up your Hostinger VPS

Instructions for buying the server the Kona Ice automation system runs on, and
handing over access so we can install it.

**Time needed:** about 20 minutes, most of it waiting for the server to build.
**What you end up with:** a server you own, under your own billing, that we
install and maintain. If you ever change providers or agencies, the server and
everything on it stays yours.

---

## Before you start

Have to hand:

- an email address you control (this becomes the account login)
- a credit card or PayPal
- about 20 minutes

**Do not send us any passwords.** Step 6 sets up access in a way that never
requires it. If anyone asks you to send a server password over WhatsApp or
email, that is worth questioning — including us.

---

## Step 1 — Create the Hostinger account

1. Go to **hostinger.com**
2. Click **Sign up** (top right) and register with your email address
3. Verify the email — they send a confirmation link
4. **Turn on two-factor authentication straight away**: click your profile icon
   → **Account information** → **Security** → enable 2FA

That last one matters more than it sounds. This account will hold the server
running your billing automation. If someone gets into the account, they get the
server; 2FA is what stops a leaked password being enough on its own.

---

## Step 2 — Buy the VPS

1. From the top menu choose **VPS Hosting**
2. Select the **KVM 4** plan

Check the plan card shows roughly these specs before you buy — this is the size
the system is built for:

| | |
|---|---|
| vCPU cores | 4 |
| RAM | 16 GB |
| Storage | ~200 GB NVMe |

Anything smaller will run it but leaves less room as your event history grows.

### Choosing the billing term

Hostinger discounts long terms heavily and the renewal price is higher than the
introductory one. A 24-month term is usually the best value, but **check what
the renewal price will be** before committing — it is shown in smaller print
under the headline price. Either way this is your call, not a technical one.

### What to skip at checkout

Hostinger offers add-ons during checkout. You do **not** need:

- **Managed hosting / priority support** — we do the server management
- **Website builder**
- **Extra backups** — worth considering later, but not required to start

A domain name is genuinely useful — see Step 5.

---

## Step 3 — Choose the server location

Pick the location **closest to Baltimore**. Hostinger usually offers several US
data centres; take the East Coast one (Boston or New York) if it appears,
otherwise the nearest US option.

This affects how quickly the dashboard responds for your office. Any US location
is fine; a European one would be noticeably slower every time someone clicks.

---

## Step 4 — Choose the operating system

This is the one step where the wrong choice means we have to redo it, so it is
worth being careful.

When Hostinger asks what to install, look for the **Applications** or **OS with
control panel** tab and choose:

> **Ubuntu 24.04 with Docker**

If you only see plain operating systems, choose **Ubuntu 24.04 LTS** — we can
install the rest ourselves. Just tell us which one you picked.

**Do not** choose:

- Windows Server
- anything with cPanel, Plesk, CyberPanel or similar — those are for websites and
  conflict with what we install
- CentOS, AlmaLinux, Debian or other Linux flavours

Then set a **root password** when prompted. Use a long random one, store it in
your password manager, and don't send it to anyone. You will not need to type it
again — Step 6 uses a different mechanism.

The server takes about 5 minutes to build. Hostinger emails you when it's ready.

---

## Step 5 — A domain name (recommended)

Not required to get running, but it makes a real difference and is worth doing
now rather than later.

**Without a domain**, the dashboard is reached at a bare IP address like
`http://31.97.54.1:8081`. That works, but:

- your staff's login password crosses the internet unencrypted
- the browser refuses to allow the microphone and camera on an unencrypted page,
  so recording cash takings and photographing cheques in the page need a manual
  browser setting on every office computer

**With a domain** — something like `ops.konaicebaltimore.com` — the system gets a
proper security certificate automatically, the padlock appears, logins are
encrypted, and the voice and camera features work everywhere including phones,
with no per-computer setup.

You can either buy one during Hostinger checkout (often bundled free for the
first year) or use a domain you already own. If you already own one, we just
need you to add one DNS record pointing at the server — we'll send you the exact
values, it's two minutes of work.

---

## Step 6 — Give us access (safely)

Once the server is ready, we need to log into it to install the system. There
are two ways, and the first is better.

### Preferred: add our SSH key

An SSH key is a cryptographic credential. You paste in a public key we send you;
it lets us log in without any password ever existing to be intercepted, and you
can revoke it in one click without changing anything else.

1. Ask us for our **public key** — we'll send a single line of text starting
   `ssh-ed25519` or `ssh-rsa`
2. In hPanel go to **VPS** → your server → **Settings** → **SSH Keys**
3. Click **Add SSH key**, paste the line, give it a name like `dev team`, save

That's it. Nothing secret ever travels over chat.

### Alternative: temporary password, changed after

If the SSH key route gives you trouble, send the server's **IP address** and the
root password through a password manager's secure-share feature (1Password,
Bitwarden and LastPass all have one that expires after viewing) — **not** over
WhatsApp or email. Tell us when you've sent it, and we'll switch the server to
key-based access and confirm the password no longer works.

### What we need from you either way

- the server's **IP address** (shown on the VPS page in hPanel)
- confirmation of which **operating system** you chose
- the **domain name**, if you set one up

---

## Step 7 — What happens next

Once we have access, we install and configure everything. That takes us a couple
of hours and needs nothing further from you.

We will send you back:

- the dashboard address to log in at
- your administrator username and password for the system itself (separate from
  anything above)
- confirmation that the nightly automation is running

From that point, updates happen automatically — when we release an improvement it
reaches your server within about five minutes, with no downtime and nothing for
you to do.

---

## What this costs you, ongoing

- **The VPS**: whatever term you chose, renewing at Hostinger's renewal rate.
  This is the only fixed hosting cost.
- **The domain**, if you bought one: usually $10–20 a year.
- **OpenAI usage**: the system reads cheque photos and classifies events using
  OpenAI. This is billed separately on the API account and is typically a few
  dollars a month at your event volume.

Nothing else. There are no per-user or per-event fees in the system itself.

---

## Questions people usually ask

**Can I use a shared hosting plan instead? It's cheaper.**
No. Shared hosting only runs websites. This system runs a database, a background
job scheduler and several services that need a real server. VPS is the minimum.

**What if I want to move it elsewhere later?**
Everything is standard, portable software and the code is in your repository.
It can be moved to any provider with a few hours' work — you are not locked in.

**Who can see my data?**
The server is yours. We access it to install and maintain the system. Your event
and financial data lives in a database on that server, not on ours.

**What if the server goes down?**
Hostinger handles the hardware. The system restarts itself automatically after a
reboot. If something needs attention, we monitor for it — but do tell us if the
dashboard stops loading.

**Do I need to know any of this to use it day to day?**
No. After Step 7 you open a web address, log in, and use it. Everything above is
one-time setup.

---

## Checklist

- [ ] Hostinger account created, email verified
- [ ] Two-factor authentication turned on
- [ ] KVM 4 purchased
- [ ] Location: nearest US data centre
- [ ] OS: **Ubuntu 24.04 with Docker**
- [ ] Root password saved in a password manager
- [ ] Domain purchased or chosen (optional but recommended)
- [ ] Our SSH key added, or credentials sent via secure share
- [ ] IP address, OS choice and domain sent to us
