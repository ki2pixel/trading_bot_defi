import sys
import os
import pytest
from unittest.mock import MagicMock

# Ajouter le répertoire racine au path pour importer les modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from defi_vault_trader import MOCK_PRICES

# Adresses fictives valides pour les tests
HOT_ADDR = "0x1111111111111111111111111111111111111111"
COLD_ADDR = "0x2222222222222222222222222222222222222222"
VAULT_ADDR = "0x3333333333333333333333333333333333333333"
WANT_ADDR = "0x4444444444444444444444444444444444444444"


@pytest.fixture(autouse=True)
def reset_mock_prices():
    """Nettoie les prix mockés avant et après chaque test."""
    MOCK_PRICES.clear()
    yield
    MOCK_PRICES.clear()


def setup_mock_w3(preview_redeem_value=99 * (10**6), price_share_value=1 * (10**18)):
    """Crée un mock Web3 avec un vault et un token want configurés."""
    mock_w3 = MagicMock()
    
    # Mock du contrat want
    mock_want = MagicMock()
    mock_want.functions.symbol.return_value.call.return_value = "USDC"
    mock_want.functions.decimals.return_value.call.return_value = 6
    mock_want.functions.balanceOf.return_value.call.return_value = 0
    
    # Mock du vault
    mock_vault = MagicMock()
    mock_vault.address = VAULT_ADDR
    mock_vault.functions.want.return_value.call.return_value = WANT_ADDR
    mock_vault.functions.balanceOf.return_value.call.return_value = 100 * (10**18)
    mock_vault.functions.previewRedeem.return_value.call.return_value = preview_redeem_value
    mock_vault.functions.getPricePerFullShare.return_value.call.return_value = price_share_value
    mock_vault.functions.allowance.return_value.call.return_value = 0
    
    # Routage du constructeur de contrat
    def contract_side_effect(address, abi):
        if address == WANT_ADDR:
            return mock_want
        return mock_vault
        
    mock_w3.eth.contract.side_effect = contract_side_effect
    return mock_w3, mock_vault
