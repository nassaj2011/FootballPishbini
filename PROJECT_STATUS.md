# FootballPishbini Project Status

## Current Version
Stable production version before multi-league redesign.

## Git Status

Main stable tag:
- v1-production-before-multileague

Archive tag:
- v1-archive-ready

Current development branch:
- feature/multileague-platform

## Production Backup

Archived database:

archive/football_wc2026_archive.db

This file contains the World Cup 2026 production database snapshot before future redesign.

## Current Architecture

Technology:
- Python
- FastAPI
- SQLAlchemy
- SQLite

Main files:
- main.py
- database.py
- templates/
- static/
- requirements.txt
- liara.json

## Database (Current Version)

Tables:
- users
- matches
- predictions
- audit_logs
- match_result_revisions
- system_settings

## Admin Access

Admin is separated from database users.

Configuration:
- ADMIN_USERNAME
- ADMIN_PASSWORD

Default development values are defined in main.py.
Production values should be stored as environment variables.

## Liara Deployment

Application:
- footballpishbini

Platform:
- Python

Deployment configuration:
- liara.json

## Important Notes

- Current version is a single competition prediction platform.
- Future multi-league development should start from feature/multileague-platform.
- Do not modify production tags.
- Database redesign should be done through migration, not direct destructive changes.