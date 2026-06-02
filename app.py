import os
import logging
import statistics
from datetime import datetime, timezone, timedelta

import csv
import io
from flask import Flask, render_template_string, redirect, Response, request, stream_with_context

import db
from monitoring import init_sentry
from gmgn_client import GmgnClient, get_sol_usd as _get_sol_usd
from scheduler import HeapScheduler
from scanner import Scanner, HotMonitor
from executor import TradeExecutor, TradeMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

init_sentry()

app = Flask(__name__)
BRT = timedelta(hours=-3)