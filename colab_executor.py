#!/usr/bin/env python3
"""
Colab Auto-Executor Service
Runs on Oracle Cloud to automatically execute Colab notebooks after upload.
Listens for gist notifications, opens Colab in headless browser, triggers execution.
"""

import os
import sys
import json
import time
import logging
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import threading

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Execution queue
execution_queue = []
current_runtime = None

def setup_driver():
    """Configure headless Chrome with Colab-compatible options"""
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    # Auto-install chromedriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.chrome import ChromeDriverManager
    
    driver = webdriver.Chrome(
        service=ChromeService(),
        options=chrome_options
    )
    return driver

def execute_colab_notebook(gist_id: str, owner: str = 'Bamove6969') -> dict:
    """
    Opens Colab notebook in headless browser and triggers auto-execute.
    The notebook has auto-execute code in the first cell.
    """
    colab_url = f'https://colab.research.google.com/gist/{owner}/{gist_id}/Cloud_GPU_Matcher_v4_Stable.ipynb'
    
    logger.info(f"Opening Colab: {colab_url}")
    driver = None
    
    try:
        driver = setup_driver()
        driver.get(colab_url)
        
        # Wait for runtime to connect (Colab needs time to spin up GPU)
        logger.info("Waiting for Colab runtime to connect...")
        WebDriverWait(driver, 120).until(
            lambda d: 'Runtime connected' in d.page_source or 
                      'connected' in d.page_source.lower()
        )
        logger.info("Runtime connected!")
        
        # The notebook has auto-execute code in cell 1
        # Just need to wait for it to trigger automatically
        # But we can also manually trigger Run All as backup
        logger.info("Waiting for auto-execute to trigger...")
        time.sleep(5)
        
        # Backup: Manually trigger Run All if auto-execute didn't fire
        try:
            run_all_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'colab-menu-action[title*="Run all"]'))
            )
            run_all_btn.click()
            logger.info("Manually triggered Run All")
        except Exception as e:
            logger.info(f"Auto-execute should be running: {e}")
        
        # Monitor execution progress
        logger.info("Monitoring execution...")
        start_time = time.time()
        max_wait = 600  # 10 minutes max
        
        while time.time() - start_time < max_wait:
            try:
                # Check if execution completed
                page_source = driver.page_source
                
                if 'Pipeline complete' in page_source or 'Results sent' in page_source:
                    logger.info("✓ Pipeline completed successfully!")
                    return {
                        'status': 'success',
                        'gist_id': gist_id,
                        'message': 'Notebook executed, results sent to backend'
                    }
                
                # Check for errors
                if 'Error' in page_source and 'execution_count' in page_source:
                    logger.warning("Execution error detected")
                    
            except Exception as e:
                logger.debug(f"Monitor check: {e}")
            
            time.sleep(10)
        
        # Timeout but may still be running
        logger.info("Execution monitoring timeout - notebook may still be running")
        return {
            'status': 'timeout',
            'gist_id': gist_id,
            'message': 'Monitoring timeout, but notebook may still be executing'
        }
        
    except Exception as e:
        logger.error(f"Execution failed: {e}", exc_info=True)
        return {
            'status': 'error',
            'gist_id': gist_id,
            'error': str(e)
        }
    finally:
        if driver:
            driver.quit()

@app.route('/execute', methods=['POST'])
def queue_execution():
    """Queue a notebook for execution"""
    data = request.json
    gist_id = data.get('gist_id')
    owner = data.get('owner', 'Bamove6969')
    
    if not gist_id:
        return jsonify({'error': 'gist_id required'}), 400
    
    execution_queue.append({'gist_id': gist_id, 'owner': owner})
    logger.info(f"Queued gist {gist_id} for execution (queue size: {len(execution_queue)})")
    
    return jsonify({
        'status': 'queued',
        'gist_id': gist_id,
        'queue_position': len(execution_queue)
    })

@app.route('/status')
def status():
    """Get executor status"""
    return jsonify({
        'queue_size': len(execution_queue),
        'current_runtime': current_runtime,
        'service': 'colab-executor',
        'version': '1.0'
    })

def process_queue():
    """Background worker to process execution queue"""
    global current_runtime
    
    while True:
        if execution_queue:
            task = execution_queue.pop(0)
            current_runtime = task['gist_id']
            
            logger.info(f"Executing gist: {task['gist_id']}")
            result = execute_colab_notebook(task['gist_id'], task['owner'])
            
            logger.info(f"Execution result: {result}")
            current_runtime = None
        else:
            time.sleep(5)

if __name__ == '__main__':
    # Start background worker
    worker_thread = threading.Thread(target=process_queue, daemon=True)
    worker_thread.start()
    
    logger.info("Colab Executor Service starting on port 5000...")
    app.run(host='0.0.0.0', port=5000)
