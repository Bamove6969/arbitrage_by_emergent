#!/usr/bin/env python3
"""
Colab Auto-Executor (Playwright)

Runs inside the universal container. Receives a Gist ID, opens the Colab gist
URL in headless Chromium, waits for the T4 runtime to connect, and triggers
Run All. The notebook itself contains an auto-execute cell as a primary path;
this service is the backup + status monitor.
"""

import os
import json
import time
import asyncio
import logging
import threading
from queue import Queue

from flask import Flask, request, jsonify
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

NOTEBOOK_NAME = os.environ.get('COLAB_NOTEBOOK_NAME', 'Cloud_GPU_Matcher_v4_Stable.ipynb')
DEFAULT_OWNER = os.environ.get('GIST_OWNER', 'Bamove6969')

execution_queue: "Queue[dict]" = Queue()
current_job: dict | None = None


async def _execute_colab(gist_id: str, owner: str) -> dict:
    colab_url = f'https://colab.research.google.com/gist/{owner}/{gist_id}/{NOTEBOOK_NAME}'
    logger.info(f'Opening Colab: {colab_url}')

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        ctx = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await ctx.new_page()
        try:
            await page.goto(colab_url, wait_until='domcontentloaded', timeout=60_000)

            # Wait up to 2 min for the T4 runtime to attach
            logger.info('Waiting for Colab runtime to connect...')
            try:
                await page.wait_for_function(
                    "document.body.innerText.toLowerCase().includes('connected') "
                    "|| document.body.innerText.toLowerCase().includes('runtime')",
                    timeout=120_000,
                )
            except Exception as e:
                logger.warning(f'Runtime wait timed out: {e}')

            # Trigger Run All as a backup to the notebook's auto-execute cell
            try:
                await page.keyboard.press('Control+F9')
                logger.info('Triggered Run All (Ctrl+F9)')
            except Exception as e:
                logger.info(f'Ctrl+F9 trigger failed (auto-execute should still run): {e}')

            # Monitor for completion / error markers for up to 10 minutes
            deadline = time.time() + 600
            while time.time() < deadline:
                body = (await page.inner_text('body')).lower()
                if 'pipeline complete' in body or 'results returned through the ngrok tunnel' in body:
                    logger.info('Pipeline complete signal observed.')
                    return {'status': 'success', 'gist_id': gist_id}
                await asyncio.sleep(10)

            logger.info('Monitoring window elapsed; notebook may still be running.')
            return {'status': 'timeout', 'gist_id': gist_id}
        finally:
            await ctx.close()
            await browser.close()


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@app.route('/execute', methods=['POST'])
def queue_execution():
    data = request.json or {}
    gist_id = data.get('gist_id')
    owner = data.get('owner', DEFAULT_OWNER)
    if not gist_id:
        return jsonify({'error': 'gist_id required'}), 400
    job = {'gist_id': gist_id, 'owner': owner}
    execution_queue.put(job)
    return jsonify({'status': 'queued', 'gist_id': gist_id, 'queue_size': execution_queue.qsize()})


@app.route('/status')
def status():
    return jsonify({
        'queue_size': execution_queue.qsize(),
        'current_job': current_job,
        'notebook': NOTEBOOK_NAME,
        'service': 'colab-executor',
        'backend': 'playwright',
    })


def _worker():
    global current_job
    while True:
        job = execution_queue.get()
        current_job = job
        try:
            logger.info(f'Executing gist: {job["gist_id"]}')
            result = _run_async(_execute_colab(job['gist_id'], job['owner']))
            logger.info(f'Execution result: {result}')
        except Exception as e:
            logger.error(f'Execution failed: {e}', exc_info=True)
        finally:
            current_job = None
            execution_queue.task_done()


if __name__ == '__main__':
    threading.Thread(target=_worker, daemon=True).start()
    logger.info(f'Colab Executor (Playwright) starting on :5000 -- notebook={NOTEBOOK_NAME}')
    app.run(host='0.0.0.0', port=5000)
