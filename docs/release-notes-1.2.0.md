# Odoo MCP v1.2.0 — What a week!

Hi there,

What a first week. Since launching Odoo MCP, we've seen people do things with their Odoo data we didn't even anticipate — building interactive dashboards by combining MCP with other tools, automating CRM workflows, bulk-updating records with natural language. It's been incredible to watch.

Your feedback has been amazing, and we've been shipping fast to keep up. Here's what's new:

## What's new

**10x more results per query**
Search now returns up to 100 records by default (was 10). No more asking Claude to "show the next page" five times in a row.

**Invite your team**
You can now invite colleagues to connect to the same Odoo instance. Everyone uses their own API key with their own Odoo permissions — so your sales team and warehouse team each see exactly what they should. Go to the **Team** page in your setup panel to send invite links. (This feature is fresh — let us know if you run into any issues.)

**Team dashboard**
See who's on your team, their connection status, and how they're using the tool. (Still being polished — feedback welcome.)

**Better setup flow**
The setup page now walks you through every step — including the OAuth sign-in popup that confused some of you (sorry about that!).

**Rock-solid infrastructure**
We've moved to zero-downtime deploys, so future updates won't disconnect you anymore. We know the frequent disconnects during this first week were frustrating — that should be fixed now.

## Known issues we're working on

- **AI data governance**: We're working on giving you more control over what data Claude can access and how it's used. If this is important for your organization, we'd love to hear your requirements.
- **Self-hosted Odoo instances**: Some users with self-hosted Odoo (not on odoo.com) experienced connection issues. We've improved error messages and URL handling, but let us know if you're still having trouble.

## One thing we need you to do: reconnect (10 seconds)

To pick up these changes, please disconnect and reconnect your Odoo MCP connector:

1. Go to Claude → MCP connectors
2. Click the **...** menu on Odoo → **Disconnect**
3. Click **Connect** again and sign in

That's it. Your data and settings are safe — this just refreshes the connection.

## Talk to us

We're building this for you, and we want to hear from you:

- **Something broken?** Tell us — we usually fix it the same day.
- **Feature idea?** We're all ears. Some of our best improvements came from user suggestions this week.
- **Love it?** Tell a colleague. The invite system makes it easy to get your team on board.

Just reply to this email or reach out at **rutger@pantalytics.com**.

Thanks for being part of this from the start,
Rutger & Daniel — Pantalytics
