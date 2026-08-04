# Granting hPanel access

How to give the development team access to manage the VPS from Hostinger's
control panel, what that access covers, and how to remove it.

**Time needed:** about 5 minutes.

---

## Why this is needed, when SSH already works

The SSH key you added lets us work *inside* the server — install the system,
deploy updates, read logs, restore a database. That covers the day-to-day.

What it cannot do is anything about the server *itself*, because those controls
live in Hostinger's panel rather than on the machine:

| Task | Needs hPanel |
|---|---|
| Take a snapshot before a risky change | ✅ |
| Restore a snapshot after something goes wrong | ✅ |
| **Emergency console when SSH is unreachable** | ✅ |
| Reboot a server that has stopped responding | ✅ |
| Upgrade the plan later (KVM 4 → KVM 8) | ✅ |
| Reinstall the operating system | ✅ |
| Provider-level firewall rules | ✅ |
| Check CPU / memory / bandwidth history | ✅ |

The one that matters most is the **emergency console**. If a firewall rule or a
network change ever locks SSH out — uncommon, but it happens — the browser
console in hPanel is the only way back in. Without it, the server stays down
until you are personally available to click a button, which could be overnight
or over a weekend.

The second is **snapshots**. You asked how backups work; part of that answer is
Hostinger's whole-machine snapshots, and we cannot take one before a risky
change or restore one after a bad one without panel access.

---

## What we would and would not do with it

**We would:** take snapshots before major changes, restore one if a change goes
wrong, use the emergency console if SSH breaks, reboot an unresponsive server,
and read the resource graphs when something seems slow.

**We would not:** change your billing details, buy or cancel services, alter
your other domains or websites, or make any purchase. If something needs buying
— a plan upgrade, extra backup storage — we will ask you first, every time.

---

## Before you start

Both of us need a free Hostinger account for the invitation to reach, and the
email has to match exactly:

- **Mohsin** — `muhammadmohsinkhatri@gmail.com`
- **Muneer** — `muneerhanif7@gmail.com`

We have created these already. You should not need to do anything about it —
just use the addresses above exactly as written.

---

## Steps

1. Log in to **hpanel.hostinger.com**

2. Click your **profile icon** (top right) → **Account information**
   *(some accounts show this as **Account Sharing** directly in that menu)*

3. Find the section called **Account Sharing** — sometimes labelled
   **Manage access**, **Share access** or **Team members** depending on which
   version of the panel your account is on

4. Click **Add user** / **Invite**, and enter the first email:

   ```text
   muhammadmohsinkhatri@gmail.com
   ```

5. If it offers a choice of scope, grant access to the **VPS** only. If it is
   all-or-nothing for the account, that is fine too — see the note below.

6. Send the invitation, then **repeat steps 4–5** for:

   ```text
   muneerhanif7@gmail.com
   ```

7. Both of us get an email and accept. Tell us once you have sent them and we
   will confirm they arrived.

**If you cannot find the option:** Hostinger moves it between panel versions.
Open the help chat in hPanel (bottom-right) and type "share account access" —
support will point you to it in a minute or two. It is a standard feature and
they answer this constantly.

---

## Two of us, not one

Please add both addresses rather than one shared login.

Separate access means each of us can be removed independently, and the audit
trail shows who did what. A single shared account gives you one switch that cuts
off everyone, and no way to tell two people's actions apart.

---

## What stays entirely yours

- **Ownership.** The account, the server and everything on it remain in your
  name. Account sharing does not transfer anything.
- **Billing.** Renewals, payment methods and invoices stay yours alone.
- **The kill switch.** You can revoke either of us at any moment, without
  warning us and without affecting anything else.
- **Your password.** Sharing does not reveal it, and we never need it.

---

## How to remove access

Same screen you added it on:

**Profile icon → Account information → Account Sharing → remove the user.**

Effective immediately. If you would also like the SSH keys removed at the same
time — a full disconnection — that is **VPS → Settings → SSH Keys**, delete the
entries named "Mohsin" and "Muneer". Between those two steps, our access is
gone completely.

Worth doing at the end of the engagement, whenever that is. Ask us and we will
walk you through it rather than leave it sitting there.

---

## If you would rather not

Entirely reasonable, and the system runs fine without it. The trade is response
time: anything in that first table becomes a message to you and a wait for you
to click it.

A middle option, if you prefer: leave it un-granted for now, and add it only at
the moment something needs it — a plan upgrade, or an emergency. It takes five
minutes whenever you choose. The only real cost is that emergencies are exactly
when five minutes of setup is hardest to arrange.

---

## Checklist

- [ ] Logged in to hPanel
- [ ] Found Account Sharing (or asked support where it is)
- [ ] Invited `muhammadmohsinkhatri@gmail.com`
- [ ] Invited `muneerhanif7@gmail.com`
- [ ] Scoped to the VPS if the option was offered
- [ ] Told us the invitations are sent
