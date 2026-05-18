#!/usr/bin/env python3
"""
Main Orchestration Script - Ties all components together
Automated pipeline: Colab Upload → Execute → Fetch Results → LLM Verification → HTML Report
"""
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# Setup logging - handle both container and local
log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    Path('/app/reports').mkdir(parents=True, exist_ok=True)
    log_handlers.append(logging.FileHandler('/app/reports/orchestrator.log'))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger(__name__)

# Import components
from backend.colab_executor import upload_and_execute
from backend.llm_parallel_workers import run_parallel_llm_verification
from backend.semantic_matcher import run_semantic_matching
from backend.html_report_generator import generate_html_report
from backend.websocket_colab import colab_ws_handler


class ArbitrageOrchestrator:
    """
    Main orchestrator for the complete arbitrage pipeline
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.notebook_path = self.config.get('notebook_path', '/app/Cloud_GPU_Matcher_v4_Stable.ipynb')
        self.reports_dir = Path(self.config.get('reports_dir', '/app/reports'))
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Pipeline state
        self.state = {
            'step': 'initialized',
            'started_at': None,
            'completed_at': None,
            'colab_file_id': None,
            'markets_fetched': 0,
            'pairs_matched': 0,
            'llm_verified': 0,
            'html_report_path': None
        }
        
    async def run_pipeline(self, trigger: str = 'manual') -> Dict[str, Any]:
        """
        Execute the complete arbitrage pipeline
        """
        self.state['started_at'] = datetime.now().isoformat()
        logger.info("=" * 60)
        logger.info("🚀 Starting Arbitrage Pipeline")
        logger.info(f"Trigger: {trigger}")
        logger.info("=" * 60)
        
        try:
            # Step 1: Upload notebook to Colab and trigger execution
            logger.info("\n📤 Step 1: Uploading notebook to Colab...")
            self.state['step'] = 'colab_upload'
            
            colab_result = await self._upload_to_colab()
            
            if not colab_result.get('success'):
                raise Exception(f"Colab upload failed: {colab_result.get('error')}")
            
            self.state['colab_file_id'] = colab_result.get('file_id')
            logger.info(f"✓ Notebook uploaded: {self.state['colab_file_id']}")
            
            # Step 2: Wait for Colab to fetch markets via WebSocket
            logger.info("\n🔄 Step 2: Waiting for Colab to fetch markets...")
            self.state['step'] = 'waiting_colab'
            
            markets = await self._wait_for_colab_markets_request()
            self.state['markets_fetched'] = len(markets)
            logger.info(f"✓ Sent {len(markets)} markets to Colab")
            
            # Step 3: Wait for Colab to return matched pairs
            logger.info("\n⏳ Step 3: Waiting for Colab processing...")
            self.state['step'] = 'waiting_colab_results'
            
            colab_matches = await self._wait_for_colab_results(timeout=600)
            self.state['pairs_matched'] = len(colab_matches)
            logger.info(f"✓ Received {len(colab_matches)} matched pairs from Colab")
            
            # Step 4: Run semantic matching to filter to identical questions
            logger.info("\n🔍 Step 4: Running semantic matching...")
            self.state['step'] = 'semantic_matching'
            
            semantic_matches = await run_semantic_matching(colab_matches, threshold=0.75)
            logger.info(f"✓ Found {len(semantic_matches)} semantic matches")
            
            # Step 5: Run parallel LLM verification
            logger.info("\n🤖 Step 5: Running parallel LLM verification...")
            self.state['step'] = 'llm_verification'
            
            llm_verified = await run_parallel_llm_verification(semantic_matches[:2000])
            self.state['llm_verified'] = len(llm_verified)
            logger.info(f"✓ LLM verified {len(llm_verified)} exact matches")
            
            # Step 6: Generate HTML report
            logger.info("\n📊 Step 6: Generating HTML report...")
            self.state['step'] = 'report_generation'
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            report_path = self.reports_dir / f"arbitrage_report_{timestamp}.html"
            
            generate_html_report(llm_verified, str(report_path))
            self.state['html_report_path'] = str(report_path)
            logger.info(f"✓ Report saved: {report_path}")
            
            # Pipeline complete
            self.state['step'] = 'completed'
            self.state['completed_at'] = datetime.now().isoformat()
            
            logger.info("\n" + "=" * 60)
            logger.info("✅ Pipeline Complete!")
            logger.info(f"Total matches: {len(llm_verified)}")
            logger.info(f"Report: {report_path}")
            logger.info("=" * 60)
            
            return {
                'success': True,
                'state': self.state,
                'matches': llm_verified,
                'report_path': str(report_path)
            }
            
        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            self.state['step'] = 'error'
            self.state['error'] = str(e)
            
            return {
                'success': False,
                'state': self.state,
                'error': str(e)
}

    async def _upload_to_colab(self) -> Dict[str, Any]:
        """Upload notebook to Colab via GitHub (runs on host)"""
        import os
        import subprocess

        notebook_path = self.notebook_path
        notebook_name = os.path.basename(notebook_path)
        logger.info(f"Pushing notebook to GitHub/Colab: {notebook_path}")

        try:
            # Get git token from remote URL
            result = subprocess.run(
                ['git', 'remote', 'get-url', 'origin'],
                capture_output=True, text=True,
                cwd='/home/droid/projects/arbitrage-calculator-main'
            )
            remote_url = result.stdout.strip()
            github_token = ''
            if 'Bamove6969:' in remote_url:
                github_token = remote_url.split('Bamove6969:')[1].split('@')[0]

            if not github_token:
                return {'success': False, 'error': 'No GitHub token found'}

            # Read notebook content
            with open(notebook_path, 'r') as f:
                notebook_content = f.read()

            # Git add, commit, push
            subprocess.run(['git', 'fetch', 'origin'], capture_output=True, cwd='/home/droid/projects/arbitrage-calculator-main')
            subprocess.run(['git', 'checkout', 'origin/main', '--', notebook_name], capture_output=True, cwd='/home/droid/projects/arbitrage-calculator-main')

            with open(f'/home/droid/projects/arbitrage-calculator-main/{notebook_name}', 'w') as f:
                f.write(notebook_content)

            subprocess.run(['git', 'add', notebook_name], capture_output=True, cwd='/home/droid/projects/arbitrage-calculator-main')
            subprocess.run(
                ['git', 'commit', '-m', f'Auto: Upload to Colab {datetime.now().isoformat()}'],
                capture_output=True, cwd='/home/droid/projects/arbitrage-calculator-main'
            )
            result = subprocess.run(
                ['git', 'push', 'origin', 'main'],
                capture_output=True, text=True, cwd='/home/droid/projects/arbitrage-calculator-main'
            )

            if result.returncode != 0:
                logger.error(f"Git push failed: {result.stderr}")
                return {'success': False, 'error': result.stderr}

            # Colab URL - public repo, no auth needed
            colab_url = f'https://colab.research.google.com/github/Bamove6969/Prediction_Market_Arbitrage_System/blob/main/{notebook_name}'

            logger.info(f"Pushed notebook to GitHub")
            logger.info(f"Colab URL: {colab_url}")

            return {
                'success': True,
                'file_id': 'pushed',
                'colab_url': colab_url,
                'message': 'Notebook pushed to GitHub and opened in Colab'
            }

        except Exception as e:
            logger.error(f"Failed to upload to Colab: {e}")
            return {'success': False, 'error': str(e)}

    async def _wait_for_colab_markets_request(self) -> List[Dict]:
        """Wait for Colab to request markets via WebSocket"""
        from backend.scanner import get_cached_markets
        
        # Get cached markets
        markets = get_cached_markets()
        
        if not markets:
            logger.warning("No cached markets found, triggering scan...")
            from backend.scanner import run_scan
            await run_scan()
            markets = get_cached_markets()
        
        return markets or []
    
    async def _wait_for_colab_results(self, timeout: int = 600) -> List[Dict]:
        """Wait for Colab to send results via WebSocket"""
        results = await colab_ws_handler.wait_for_results(timeout=timeout)
        
        if results:
            return results.get('pairs', [])
        else:
            logger.warning("No results received from Colab, using fallback...")
            return []
    
    def get_state(self) -> Dict[str, Any]:
        """Get current pipeline state"""
        return self.state


async def main():
    """Main entry point"""
    config = {
        'notebook_path': '/app/Cloud_GPU_Matcher_v4_Stable.ipynb',
        'reports_dir': '/app/reports'
    }
    
    orchestrator = ArbitrageOrchestrator(config)
    result = await orchestrator.run_pipeline(trigger='manual')
    
    if result['success']:
        print(f"\n✅ Success! Report: {result['report_path']}")
        return 0
    else:
        print(f"\n❌ Failed: {result.get('error')}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
