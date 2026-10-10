\# AGENTS.md



\## Project



This repository is the BuenoDMR fork of HBlink4.



Upstream:

https://github.com/n0mjs710/HBlink4



Fork:

https://github.com/2dbueno/HBlink4



Development branch:

bueno



\## Goal



Extend HBlink4 into a small and secure DMR network that can be administered through a browser interface.



The initial production server is a Raspberry Pi 3.



\## Current network rules



BuenoDMR currently uses:



\- UDP port 62031

\- TS1 disabled

\- TS2 enabled

\- TG100 only

\- WPSD clients using Custom DMR Network

\- direct routing:

&#x20; TGRewrite0=2,100,2,100,1



Do not change these rules unless explicitly requested.



\## Development rules



Before editing code:



1\. Inspect the existing implementation.

2\. Inspect relevant tests.

3\. Preserve HBlink4 upstream compatibility.

4\. Prefer small isolated changes.

5\. Avoid rewriting working HBlink4 core logic.

6\. Add or update tests for new behavior.

7\. Do not commit or push unless explicitly requested.



\## Security



Never commit:



\- production passwords

\- DMR passphrases

\- tokens

\- live config files

\- .env files

\- logs

\- runtime databases



Live files such as:



\- config/config.json

\- dashboard/config.json



must remain outside Git.



Administrative passwords must never be stored in plaintext.



\## BuenoDMR administration



The future admin interface should support:



\- authenticated admin login

\- Admin and Operator roles

\- managing DMR users

\- one DMR operator with multiple ESSIDs/hotspots

\- individual DMR passphrases

\- enable/disable users

\- password rotation

\- audit logs

\- safe configuration updates

\- rollback on failure



Authorization must always be enforced server-side.



\## Architecture



Prefer extending the existing HBlink4 dashboard rather than creating a completely separate application.



Keep dependencies lightweight because the production target includes Raspberry Pi 3.



Avoid unnecessary heavy infrastructure such as:



\- Redis

\- PostgreSQL

\- Docker

\- large frontend frameworks



unless clearly justified.



\## Git



main:

keep close to upstream HBlink4



bueno:

BuenoDMR development branch



Do not develop directly on main.

