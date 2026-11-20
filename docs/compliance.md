# Compliance and responsible use

Job Radar is a personal, low-volume tool built for one person's job search and as a portfolio project. It is not a commercial product and comes with no warranty (see the license).

## What it does and does not do

- Reads **public** APIs, RSS feeds and, only if you enable them, public HTML pages.
- Parses **job-alert emails you already receive**. It never requests a LinkedIn page, never logs in anywhere, never rotates cookies.
- Stores ad metadata (title, company, place, salary if public, description) in files on your machine.
- Does **not** collect personal data about recruiters or candidates, send messages, apply to jobs or aggregate data for third parties.

## Source terms

Only sources with a public API or RSS feed are enabled by default. freehire, Randstad Italia and EU Data Jobs are included but **disabled**: before enabling any of them, read the site's terms of use and `robots.txt` and decide for yourself. Jobicy asks users to credit the source: the link to the original ad is always kept in the queue and the spreadsheet. Requests use an honest User-Agent (`JobRadar/2.0 ...`), pauses between calls and low volume.

## Personal data

- The repository contains no personal data. `config/profile.example.yaml` describes a fictional candidate; your real `config/profile.yaml`, queues and spreadsheets are git-ignored.
- Ads published by companies are public. If you are a company or recruiter and want an ad removed from someone's local files, ask that person; nothing is sent to any server by this tool.
- If you add features that touch recruiter data, check the legal basis (GDPR art. 6) first.

## Credentials

The only credential is an optional Anthropic API key in `.env` (git-ignored, `.env.example` is the template). Never commit it.

## Not legal advice

Whether automated access is allowed depends on each site's terms and on your jurisdiction. This document describes the project's intent, not a legal opinion.
