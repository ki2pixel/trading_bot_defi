import pytest
import sys
import os

# Ajouter le répertoire racine au path pour importer les modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from defi_vault_finder import is_stable_vault, filter_and_rank_vaults

def test_is_stable_vault_true():
    # Vault avec uniquement des stablecoins connus
    vault_stable_usd = {
        "id": "curve-usdc-usdt",
        "name": "USDC-USDT Pool",
        "assets": ["USDC", "USDT"],
        "status": "active"
    }
    vault_stable_eur = {
        "id": "curve-eure-eur",
        "name": "EURE-EUR Pool",
        "assets": ["EURE", "EUR"],
        "status": "active"
    }
    
    assert is_stable_vault(vault_stable_usd) is True
    assert is_stable_vault(vault_stable_eur) is True

def test_is_stable_vault_false():
    # Vault contenant un crypto-actif non stable (ETH, WBTC)
    vault_mixed = {
        "id": "uniswap-eth-usdc",
        "name": "ETH-USDC Pool",
        "assets": ["ETH", "USDC"],
        "status": "active"
    }
    vault_no_assets = {
        "id": "empty-vault",
        "name": "Empty",
        "assets": [],
        "status": "active"
    }
    
    assert is_stable_vault(vault_mixed) is False
    assert is_stable_vault(vault_no_assets) is False

def test_filter_and_rank_vaults():
    mock_vaults = [
        {
            "id": "vault-low",
            "name": "Low APY Vault",
            "chain": "arbitrum",
            "status": "active",
            "platformId": "uniswap",
            "assets": ["USDC", "USDT"]
        },
        {
            "id": "vault-high",
            "name": "High APY Vault",
            "chain": "base",
            "status": "active",
            "platformId": "aerodrome",
            "assets": ["USDC", "MAI"]
        },
        {
            "id": "vault-inactive",
            "name": "Inactive Vault",
            "chain": "arbitrum",
            "status": "eol", # End of Life / Inactive
            "platformId": "curve",
            "assets": ["USDT", "DAI"]
        },
        {
            "id": "vault-wrong-chain",
            "name": "Ethereum Vault",
            "chain": "ethereum",
            "status": "active",
            "platformId": "curve",
            "assets": ["USDT", "USDC"]
        }
    ]
    
    mock_apys = {
        "vault-low": 0.05,        # 5%
        "vault-high": 0.12,       # 12%
        "vault-inactive": 0.20,   # Devrait être filtré car inactif
        "vault-wrong-chain": 0.15 # Devrait être filtré car sur Ethereum (non L2)
    }
    
    # Filtrer pour Arbitrum et Base uniquement, APY min = 1%
    chains = {"arbitrum", "base"}
    results = filter_and_rank_vaults(mock_vaults, mock_apys, chains=chains, min_apy=0.01)
    
    # On s'attend à avoir 2 résultats : vault-high en premier (12% APY) et vault-low en deuxième (5% APY)
    assert len(results) == 2
    assert results[0]["id"] == "vault-high"
    assert results[0]["apy"] == 0.12
    assert results[1]["id"] == "vault-low"
    assert results[1]["apy"] == 0.05

def test_calculate_break_even():
    from defi_vault_finder import calculate_break_even
    
    # Cas 1: Nouveau vault avec APY beaucoup plus grand -> Rentable
    # APY actuel: 5% (0.05), Nouveau: 15% (0.15)
    # Capital: 10000, Jours: 30
    # Zap in: 0.05%, Zap out: 0.05%, Withdraw: 0.05%, Slippage: 0.1% -> Friction totale: 0.05+0.05+0.05+2*0.1 = 0.35% (0.0035)
    is_prof, net_prof, be_apy, friction = calculate_break_even(
        current_apy=0.05,
        new_apy=0.15,
        capital=10000.0,
        days=30,
        zap_in=0.05,
        zap_out=0.05,
        withdraw=0.05,
        slippage=0.1
    )
    
    assert is_prof is True
    assert net_prof > 0
    assert friction == pytest.approx(0.35)
    
    # Cas 2: Nouveau vault avec APY légèrement plus grand mais friction absorbe le gain -> Non rentable
    # APY actuel: 5% (0.05), Nouveau: 5.5% (0.055)
    is_prof, net_prof, be_apy, friction = calculate_break_even(
        current_apy=0.05,
        new_apy=0.055,
        capital=10000.0,
        days=30,
        zap_in=0.05,
        zap_out=0.05,
        withdraw=0.05,
        slippage=0.1
    )
    assert is_prof is False
    assert net_prof < 0

