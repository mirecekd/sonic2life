#!/bin/bash
# KecWeb – start server
# Předpoklad: aktivní virtualenv (workon kecweb)

set -e

cd "$(dirname "$0")"

uvicorn app.main:app --host 0.0.0.0 --port 5005 --reload --proxy-headers --forwarded-allow-ips '*'
