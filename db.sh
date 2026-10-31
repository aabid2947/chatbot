#!/usr/bin/env bash
# Start/stop the local MySQL the chatbot backend uses.
#
# MySQL is a portable, unzipped build in ~/mysqlportable rather than a Windows
# service, because installing a service needs admin rights this account lacks.
# It listens on 127.0.0.1:3306 only.
#
# Usage:  bash db.sh          start (and create the chatbot database/user on first run)
#         bash db.sh status
#         bash db.sh stop

MY_HOME="$HOME/mysqlportable"
MY_INI_WIN='C:\Users\aadil\mysqlportable\my.ini'
MYSQLD_WIN='C:\Users\aadil\mysqlportable\bin\mysqld.exe'
MYSQL="$MY_HOME/bin/mysql.exe"
MYSQLADMIN="$MY_HOME/bin/mysqladmin.exe"
PORT=3306

is_up() {
  "$MYSQLADMIN" --host=127.0.0.1 --port=$PORT -uroot ping >/dev/null 2>&1
}

start() {
  if is_up; then
    echo "  mysql  already running"
  else
    # mysqld runs in the foreground and would die with this shell, so it is
    # launched detached and hidden via PowerShell.
    powershell.exe -NoProfile -Command \
      "Start-Process -FilePath '$MYSQLD_WIN' -ArgumentList '--defaults-file=$MY_INI_WIN' -WindowStyle Hidden" \
      >/dev/null 2>&1
    for _ in $(seq 1 30); do
      sleep 1
      is_up && break
    done
    if ! is_up; then
      echo "  mysql  FAILED to start, see $MY_HOME/mysqld.log"
      exit 1
    fi
    echo "  mysql  started"
  fi
  # Idempotent: only creates what's missing.
  "$MYSQL" --host=127.0.0.1 --port=$PORT -uroot <<'SQL'
CREATE DATABASE IF NOT EXISTS chatbot CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'chatbot'@'localhost' IDENTIFIED BY 'chatbot';
CREATE USER IF NOT EXISTS 'chatbot'@'127.0.0.1' IDENTIFIED BY 'chatbot';
GRANT ALL PRIVILEGES ON chatbot.* TO 'chatbot'@'localhost';
GRANT ALL PRIVILEGES ON chatbot.* TO 'chatbot'@'127.0.0.1';
SQL
}

stop() {
  if is_up; then
    "$MYSQLADMIN" --host=127.0.0.1 --port=$PORT -uroot shutdown && echo "  mysql  stopped"
  else
    echo "  mysql  was not running"
  fi
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) is_up && echo "  mysql  up   ($PORT)" || echo "  mysql  DOWN ($PORT)" ;;
  *) echo "usage: bash db.sh [start|stop|status]" >&2; exit 1 ;;
esac
