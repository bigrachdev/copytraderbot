"""
Hardware wallet integration for Solana (Phantom, Ledger)
"""
import logging
from typing import Optional, Dict, Tuple
import qrcode
import io
import base64

logger = logging.getLogger(__name__)


class HardwareWalletConnector:
    """Connect to hardware wallets via WalletConnect"""
    
    def __init__(self):
        self.supported_wallets = [
            'phantom',
            'ledger',
            'trezor',
            'solflare'
        ]
    
    def generate_connection_qr(self, unique_id: str) -> str:
        """Generate QR code for wallet connection"""
        try:
            # Create QR code data
            qr_data = f"solana://connect/{unique_id}"
            
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_data)
            qr.make(fit=True)
            
            # Convert to image
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to base64
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            img_base64 = base64.b64encode(buffer.getvalue()).decode()
            
            return img_base64
        
        except Exception as e:
            logger.error(f"QR code generation error: {e}")
            return None
    
    def get_phantom_connection_link(self) -> str:
        """Get Phantom wallet connection link"""
        # Phantom uses deep linking
        return "https://phantom.app/"
    
    def get_ledger_connection_info(self) -> Dict:
        """Get Ledger connection instructions"""
        return {
            'app': 'Solana',
            'derivation_path': "m/44'/501'/0'/0'",
            'address_format': 'base58'
        }
    
    async def verify_hardware_wallet_signature(self, message: str, 
                                              signature: str,
                                              public_key: str) -> bool:
        """Verify message signed by hardware wallet"""
        try:
            import nacl.signing
            vk = nacl.signing.VerifyKey(public_key)
            vk.verify(message.encode(), signature.encode())
            
            logger.info("✅ Hardware wallet signature verified")
            return True
        
        except Exception as e:
            logger.warning(f"❌ Signature verification failed: {e}")
            return False


hw_connector = HardwareWalletConnector()
