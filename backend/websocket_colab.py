"""
WebSocket Data Transfer Handler - Bidirectional communication with Colab
"""
import asyncio
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class ColabWebSocketHandler:
    """
    Handles WebSocket communication between backend and Colab notebook
    Supports:
    - Sending raw markets to Colab for processing
    - Receiving matched pairs back from Colab
    - Real-time status updates
    """
    
    def __init__(self):
        self.active_connections: Dict[str, Any] = {}
        self.markets_queue = asyncio.Queue()
        self.results_queue = asyncio.Queue()
        self.connection_ready = asyncio.Event()
        
    async def connect(self, websocket, connection_id: str = "colab"):
        """Accept WebSocket connection"""
        await websocket.accept()
        self.active_connections[connection_id] = {
            "websocket": websocket,
            "connected_at": datetime.now(),
            "status": "connected"
        }
        logger.info(f"WebSocket connected: {connection_id}")
        return websocket
    
    def disconnect(self, connection_id: str):
        """Remove connection"""
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"WebSocket disconnected: {connection_id}")
    
    async def send_markets(self, websocket, markets: list):
        """Send raw markets to Colab"""
        try:
            message = {
                "type": "markets_data",
                "markets": markets,
                "count": len(markets),
                "timestamp": datetime.now().isoformat()
            }
            await websocket.send_json(message)
            logger.info(f"Sent {len(markets)} markets to Colab")
        except Exception as e:
            logger.error(f"Failed to send markets: {e}")
            raise
    
    async def receive_results(self, websocket, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process an already-received cloud_results message and ack it."""
        try:
            if data.get("type") != "cloud_results":
                logger.warning(f"Unexpected message type: {data.get('type')}")
                return None

            results = data.get("data", [])
            logger.info(f"Received {len(results)} matched pairs from Colab")

            await self.results_queue.put({
                "pairs": results,
                "received_at": datetime.now(),
                "count": len(results),
            })

            await websocket.send_json({
                "type": "results_received",
                "message": f"Received {len(results)} pairs",
                "timestamp": datetime.now().isoformat(),
            })
            return data
        except Exception as e:
            logger.error(f"Failed to receive results: {e}")
            return None
    
    async def send_status_update(self, websocket, status: Dict[str, Any]):
        """Send status update to Colab"""
        try:
            message = {
                "type": "status_update",
                **status,
                "timestamp": datetime.now().isoformat()
            }
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send status: {e}")
    
    async def wait_for_results(self, timeout: int = 300) -> Optional[Dict[str, Any]]:
        """Wait for results from Colab with timeout"""
        try:
            results = await asyncio.wait_for(
                self.results_queue.get(),
                timeout=timeout
            )
            return results
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for Colab results")
            return None
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status"""
        return {
            "active_connections": len(self.active_connections),
            "connections": [
                {
                    "id": conn_id,
                    "connected_at": info["connected_at"].isoformat(),
                    "status": info["status"]
                }
                for conn_id, info in self.active_connections.items()
            ],
            "queues": {
                "markets_pending": self.markets_queue.qsize(),
                "results_pending": self.results_queue.qsize()
            }
        }


# Global instance
colab_ws_handler = ColabWebSocketHandler()


async def handle_colab_websocket(websocket):
    """
    Main WebSocket handler for Colab connections
    """
    connection_id = "colab"
    
    try:
        await colab_ws_handler.connect(websocket, connection_id)
        
        while True:
            # Wait for messages from Colab
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "subscribe":
                channel = data.get("channel")
                logger.info(f"Colab subscribed to: {channel}")
                
                if channel == "markets":
                    # Send markets when requested
                    from backend.scanner import get_cached_markets
                    markets = get_cached_markets()
                    await colab_ws_handler.send_markets(websocket, markets)
                    
            elif msg_type == "cloud_results":
                # Process results from Colab (data is already in `data`)
                await colab_ws_handler.receive_results(websocket, data)
                
            elif msg_type == "ping":
                await websocket.send_json({
                    "type": "pong",
                    "time": datetime.now().isoformat()
                })
                
            elif msg_type == "status_request":
                status = colab_ws_handler.get_connection_status()
                await colab_ws_handler.send_status_update(websocket, status)
                
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        colab_ws_handler.disconnect(connection_id)
