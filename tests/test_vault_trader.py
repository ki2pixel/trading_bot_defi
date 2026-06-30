import pytest
from unittest.mock import MagicMock, patch
from defi_vault_trader import (
    RPCManager,
    execute_with_retry,
    get_stablecoin_price,
    check_depeg_and_execute_strategy,
    MOCK_PRICES
)

# Adresses fictives valides pour les tests
HOT_ADDR = "0x1111111111111111111111111111111111111111"
COLD_ADDR = "0x2222222222222222222222222222222222222222"
VAULT_ADDR = "0x3333333333333333333333333333333333333333"
WANT_ADDR = "0x4444444444444444444444444444444444444444"

def test_rpc_manager_failover():
    urls = ["https://rpc1.com", "https://rpc2.com"]
    with patch('defi_vault_trader.Web3') as mock_w3:
        mock_w3_inst1 = MagicMock()
        mock_w3_inst1.is_connected.return_value = False
        
        mock_w3_inst2 = MagicMock()
        mock_w3_inst2.is_connected.return_value = True
        
        mock_w3.side_effect = [mock_w3_inst1, mock_w3_inst2]
        
        manager = RPCManager(urls)
        assert manager.w3 is None
        
        success = manager.switch_rpc()
        assert success is True
        assert manager.current_index == 1
        assert manager.w3 == mock_w3_inst2

@patch("time.sleep", return_value=None)
def test_execute_with_retry_not_calm(mock_sleep):
    mock_rpc = MagicMock()
    mock_w3 = MagicMock()
    mock_rpc.get_w3.return_value = mock_w3
    
    call_count = 0
    def dummy_func(w3):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("execution reverted: NotCalm")
        return "success"
        
    result = execute_with_retry(mock_rpc, dummy_func, max_retries=5)
    assert result == "success"
    assert call_count == 3
    assert mock_sleep.call_count == 2

@patch("time.sleep", return_value=None)
def test_execute_with_retry_failover(mock_sleep):
    mock_rpc = MagicMock()
    mock_w3 = MagicMock()
    mock_rpc.get_w3.return_value = mock_w3
    mock_rpc.switch_rpc.return_value = True
    
    call_count = 0
    def dummy_func(w3):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("ConnectionTimeoutError")
        return "success"
        
    result = execute_with_retry(mock_rpc, dummy_func, max_retries=5)
    assert result == "success"
    assert call_count == 2
    mock_rpc.switch_rpc.assert_called_once()

@patch("requests.get")
def test_get_stablecoin_price(mock_get):
    mock_get.side_effect = Exception("Offline fallback")
    
    # Test mock price
    MOCK_PRICES["USDT"] = 0.98
    assert get_stablecoin_price(None, "USDT") == 0.98
    
    # Test fallback to default
    if "USDC" in MOCK_PRICES:
        del MOCK_PRICES["USDC"]
    assert get_stablecoin_price(None, "USDC") == 1.0

def test_depeg_decision_tree_hold():
    MOCK_PRICES["USDC"] = 0.992
    
    decision = check_depeg_and_execute_strategy(None, VAULT_ADDR, HOT_ADDR)
    assert decision == "HOLD"

def setup_mock_w3(preview_redeem_value=99 * (10**6), price_share_value=1 * (10**18)):
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

@patch("defi_vault_trader.trigger_withdrawal_flow")
def test_depeg_decision_tree_moderate_exit_ok(mock_withdraw):
    MOCK_PRICES["USDC"] = 0.97
    mock_withdraw.return_value = "WITHDRAWN"
    
    # 99 USDC reçus pour 100 parts (1% slippage) -> inférieur à la limite de 2% -> Retrait
    mock_w3, mock_vault = setup_mock_w3(
        preview_redeem_value=99 * (10**6),
        price_share_value=1 * (10**18)
    )
    
    decision = check_depeg_and_execute_strategy(mock_w3, VAULT_ADDR, HOT_ADDR)
    assert decision == "WITHDRAWN"
    mock_withdraw.assert_called_once()

@patch("defi_vault_trader.trigger_withdrawal_flow")
def test_depeg_decision_tree_moderate_hold(mock_withdraw):
    MOCK_PRICES["USDC"] = 0.97
    
    # 95 USDC reçus pour 100 parts (5% slippage) -> supérieur à la limite de 2% -> HOLD
    mock_w3, mock_vault = setup_mock_w3(
        preview_redeem_value=95 * (10**6),
        price_share_value=1 * (10**18)
    )
    
    decision = check_depeg_and_execute_strategy(mock_w3, VAULT_ADDR, HOT_ADDR)
    assert decision == "HOLD"
    mock_withdraw.assert_not_called()

@patch("defi_vault_trader.trigger_withdrawal_flow")
def test_depeg_decision_tree_critical(mock_withdraw):
    MOCK_PRICES["USDC"] = 0.92
    mock_withdraw.return_value = "WITHDRAWN"
    
    mock_w3, mock_vault = setup_mock_w3()
    
    decision = check_depeg_and_execute_strategy(mock_w3, VAULT_ADDR, HOT_ADDR)
    assert decision == "WITHDRAWN"
    mock_withdraw.assert_called_once()

def test_hot_cold_wallet_not_approved():
    mock_w3, mock_vault = setup_mock_w3()
    
    # Simuler pas de solde sur le hot et pas d'approbation sur le cold
    mock_vault.functions.balanceOf.return_value.call.return_value = 0
    mock_vault.functions.allowance.return_value.call.return_value = 0
    
    from defi_vault_trader import trigger_withdrawal_flow
    with pytest.raises(PermissionError):
        trigger_withdrawal_flow(
            w3=mock_w3,
            vault_contract=mock_vault,
            shares=100 * (10**18),
            hot_wallet=HOT_ADDR,
            private_key=None,
            cold_wallet=COLD_ADDR
        )
