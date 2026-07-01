#!/usr/bin/env python3
"""
Bot DeFi Stablecoin - Vault Trader
Ce script permet d'interagir on-chain avec les vaults de Beefy Finance.
Il intègre désormais :
- Un gestionnaire de RPC avec bascule automatique (failover).
- Un mécanisme de retry exponentiel pour gérer l'erreur NotCalm().
- Une marge de sécurité de 15% sur les limites de gaz.
- Un arbre de décision à 3 niveaux face au depeg de stablecoins.
- Une séparation de portefeuilles Hot / Cold Wallet.
"""

import os
import sys
import time
import logging
import requests
from web3 import Web3
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# Configuration du module de logs
class OpSecMaskingFormatter(logging.Formatter):
    """
    Formatter qui masque automatiquement les informations sensibles
    (clés privées, tokens d'API, adresses EVM complètes) dans les logs.
    """
    def __init__(self, fmt=None, datefmt=None, style='%'):
        super().__init__(fmt, datefmt, style)
        self.patterns = []
        self.rebuild_patterns()

    def rebuild_patterns(self):
        self.patterns = []
        import os
        
        # 1. Clé privée
        pk = os.getenv("HOT_WALLET_PRIVATE_KEY")
        if pk and len(pk.strip()) > 8:
            self.patterns.append((pk.strip(), "[MASKED_PRIVATE_KEY]"))
            
        # 2. Discord Webhook URL
        discord = os.getenv("DISCORD_WEBHOOK_URL")
        if discord and len(discord.strip()) > 8:
            self.patterns.append((discord.strip(), "[MASKED_DISCORD_WEBHOOK]"))
            
        # 3. Telegram Bot Token et Chat ID
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token and len(tg_token.strip()) > 8:
            self.patterns.append((tg_token.strip(), "[MASKED_TELEGRAM_TOKEN]"))
            
        tg_chat = os.getenv("TELEGRAM_CHAT_ID")
        if tg_chat and len(tg_chat.strip()) > 2:
            self.patterns.append((tg_chat.strip(), "[MASKED_TELEGRAM_CHAT_ID]"))
            
        # 4. RPC URLs
        rpc_str = (os.getenv("L2_RPC_URLS") or "") + "," + (os.getenv("L2_RPC_URL") or "")
        urls = [u.strip() for u in rpc_str.split(",") if u.strip()]
        for url in urls:
            if len(url) > 10:
                masked = self._mask_url(url)
                self.patterns.append((url, masked))
                
        # 5. Adresses EVM (Vault, Portefeuilles)
        addr_vars = ["DEFI_VAULT_ADDRESS", "USER_WALLET_ADDRESS", "HOT_WALLET_ADDRESS", "COLD_WALLET_ADDRESS"]
        for var in addr_vars:
            addr = os.getenv(var)
            if addr and addr.strip().startswith("0x") and len(addr.strip()) == 42:
                clean_addr = addr.strip()
                masked = f"{clean_addr[:6]}...{clean_addr[-4:]}"
                variants = {clean_addr, clean_addr.lower(), clean_addr.upper()}
                try:
                    from web3 import Web3
                    variants.add(Web3.to_checksum_address(clean_addr))
                except Exception:
                    pass
                for variant in variants:
                    self.patterns.append((variant, masked))
                    
        # Trier par longueur décroissante pour éviter des remplacements partiels conflictuels
        self.patterns = sorted(list(set(self.patterns)), key=lambda x: len(x[0]), reverse=True)

    def _mask_url(self, url):
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                netloc = parsed.netloc
                if "@" in netloc:
                    _, host = netloc.split("@", 1)
                    netloc = host
                return f"{parsed.scheme}://{netloc}/***"
        except Exception:
            pass
        if len(url) > 15:
            return f"{url[:10]}...{url[-5:]}"
        return "***"

    def format(self, record):
        formatted = super().format(record)
        for sensitive, replacement in self.patterns:
            if sensitive and len(sensitive) > 4:
                formatted = formatted.replace(sensitive, replacement)
        return formatted

logger = logging.getLogger("defi_vault_trader")

# Configuration de base si lancée en CLI directement
if not logging.getLogger().handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(OpSecMaskingFormatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)


def print(*args, file=None, **kwargs):
    """
    Redirige les appels print() vers le système de logging de Python
    en analysant les tags de sévérité.
    """
    msg = " ".join(str(arg) for arg in args)
    # Nettoyer les sauts de ligne initiaux pour l'esthétique des logs
    msg_clean = msg.strip()
    if not msg_clean:
        return
        
    if file == sys.stderr or "[ERREUR]" in msg_clean or "Erreur" in msg_clean:
        logger.error(msg_clean)
    elif "[WARNING]" in msg_clean or "[WARN]" in msg_clean:
        logger.warning(msg_clean)
    elif "[GAS]" in msg_clean or "[TX]" in msg_clean or "[SIMULATION]" in msg_clean:
        logger.info(msg_clean)
    else:
        logger.info(msg_clean)

# Dictionnaire de prix de simulation (pour les tests)
MOCK_PRICES = {}

# ABI de base pour un token ERC-20
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_from", "type": "address"}, {"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transferFrom",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    }
]

# ABI pour un Vault Beefy Finance (mooVault)
BEEFY_VAULT_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "want",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "getPricePerFullShare",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "balance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_amount", "type": "uint256"}],
        "name": "deposit",
        "outputs": [],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [],
        "name": "depositAll",
        "outputs": [],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_shares", "type": "uint256"}],
        "name": "withdraw",
        "outputs": [],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [],
        "name": "withdrawAll",
        "outputs": [],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [{"name": "_shares", "type": "uint256"}],
        "name": "previewRedeem",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    # mooTokens sont aussi des tokens ERC-20
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "remaining", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_from", "type": "address"}, {"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transferFrom",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    }
]

class RPCManager:
    """
    Gère la connexion Web3 à travers une liste de RPCs avec bascule automatique (failover).
    """
    def __init__(self, rpc_urls=None):
        if rpc_urls is None:
            urls_str = os.getenv("L2_RPC_URLS") or os.getenv("L2_RPC_URL") or ""
            self.rpc_urls = [url.strip() for url in urls_str.split(",") if url.strip()]
        else:
            self.rpc_urls = rpc_urls
            
        self.current_index = 0
        self.w3 = None
        self._connect()
        
    def _connect(self):
        if not self.rpc_urls:
            self.w3 = None
            return
            
        url = self.rpc_urls[self.current_index]
        if "0.0.0.0" in url or (not url.startswith("http") and not url.startswith("ws")):
            self.w3 = None
            return
            
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': 10}))
            if w3.is_connected():
                self.w3 = w3
                print(f"[RPCManager] Connecté au RPC : {url}")
                return
        except Exception as e:
            print(f"[RPCManager] Échec de connexion à {url}: {e}", file=sys.stderr)
            
        self.w3 = None

    def get_w3(self):
        return self.w3
        
    def switch_rpc(self):
        if len(self.rpc_urls) <= 1:
            print("[RPCManager] Aucun RPC alternatif disponible.", file=sys.stderr)
            return False
            
        self.current_index = (self.current_index + 1) % len(self.rpc_urls)
        next_url = self.rpc_urls[self.current_index]
        print(f"[RPCManager] Bascule vers le RPC de secours : {next_url}", file=sys.stderr)
        self._connect()
        return self.w3 is not None

def execute_with_retry(rpc_manager, func, *args, max_retries=5, backoff_base=2, **kwargs):
    """
    Exécute une fonction Web3 avec retry exponentiel ( NotCalm() ) et failover RPC automatique.
    """
    attempt = 0
    current_backoff = backoff_base
    while attempt < max_retries:
        w3 = rpc_manager.get_w3()
        if w3 is None:
            if not rpc_manager.switch_rpc():
                raise ConnectionError("Aucun RPC fonctionnel disponible pour l'exécution.")
            continue
            
        try:
            return func(w3, *args, **kwargs)
        except Exception as e:
            err_str = str(e)
            # Cas d'erreur NotCalm de Beefy (volatilité)
            if "NotCalm" in err_str or "0x42f7c00e" in err_str:
                attempt += 1
                if attempt >= max_retries:
                    print(f"[ERREUR] Échec final après {max_retries} tentatives suite à NotCalm().", file=sys.stderr)
                    raise
                print(f"[WARNING] Volatilité détectée (NotCalm). Retentative dans {current_backoff}s (tentative {attempt}/{max_retries})...")
                time.sleep(current_backoff)
                current_backoff *= 2
            # Cas d'erreur de connexion / RPC
            elif any(err in err_str for err in ["Connection", "Timeout", "HTTP", "Response", "Provider"]):
                print(f"[WARNING] Panne RPC détectée lors de l'appel. Tentative de failover...", file=sys.stderr)
                if not rpc_manager.switch_rpc():
                    attempt += 1
                    if attempt >= max_retries:
                        raise
                    time.sleep(current_backoff)
                    current_backoff *= 2
            else:
                # Autres exceptions (ex: solde insuffisant, etc.) -> lever directement
                raise e

def send_transaction_with_safety(w3, contract_call, wallet_address, private_key=None):
    """
    Estime le gaz avec une marge de 15%, simule la transaction localement (eth_call),
    puis la signe et l'envoie si une clé privée est configurée.
    """
    from_addr = Web3.to_checksum_address(wallet_address)
    tx_fields = {
        'from': from_addr,
        'nonce': w3.eth.get_transaction_count(from_addr),
    }
    
    # Gestion des frais de gaz EIP-1559 ou standard
    try:
        base_fee = w3.eth.get_block('latest').get('baseFeePerGas', 0)
        tx_fields['maxPriorityFeePerGas'] = w3.eth.max_priority_fee
        tx_fields['maxFeePerGas'] = int(base_fee * 1.25) + tx_fields['maxPriorityFeePerGas']
    except Exception:
        tx_fields['gasPrice'] = w3.eth.gas_price

    tx = contract_call.build_transaction(tx_fields)
    
    # Estimation du gaz avec marge de sécurité de 15%
    try:
        estimated_gas = w3.eth.estimate_gas(tx)
        tx['gas'] = int(estimated_gas * 1.15)
        print(f"[GAS] Gaz estimé : {estimated_gas} -> Limite avec marge 15% : {tx['gas']}")
    except Exception as e:
        tx['gas'] = 500000 # Valeur par défaut pour L2
        print(f"[GAS] Échec de l'estimation ({e}), utilisation de la limite de repli : {tx['gas']}")
        
    # Simulation locale (eth_call)
    try:
        w3.eth.call(tx)
        print("[SIMULATION] Simulation eth_call réussie.")
    except Exception as e:
        print(f"[ERREUR SIMULATION] La transaction échouerait on-chain : {e}", file=sys.stderr)
        raise e
        
    if private_key:
        print("[TX] Signature et envoi de la transaction réelle...")
        signed_tx = w3.eth.account.sign_transaction(tx, private_key=private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        print(f"[TX] Transaction diffusée. Hash : {tx_hash.hex()}")
        print("[TX] En attente de confirmation...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print(f"[TX] Confirmée ! Bloc : {receipt.blockNumber} (Statut: {receipt.status})")
        return receipt
    else:
        print("[SIMULATION] Mode DRY-RUN (Pas de clé privée). Transaction simulée avec succès.")
        return {"status": 1, "simulation": True, "tx": tx}

# Oracle de prix stablecoins
COINGECKO_IDS = {
    "USDC": "usd-coin",
    "USDT": "tether",
    "DAI": "dai",
    "FRAX": "frax",
    "USDE": "ethena-usde",
    "SUSDE": "ethena-actived-usde",
    "LUSD": "liquity-usd",
    "MAI": "mimatic",
    "MIM": "magic-internet-money"
}

CHAINLINK_FEEDS = {
    "arbitrum": {
        "USDC": "0x50834F154630568276477e59EEA40d9AF7A08c79",
        "USDT": "0x3f3f5d9F58DE082D2C2FF8fA019F37B85CEf55E6",
        "DAI": "0xc5C8E77B397E34F22C52b0c1737E84d41C25Fd93",
    },
    "base": {
        "USDC": "0x7e86d061595188827361839598282361b181bc6B",
        "DAI": "0x591e88863f820C40409E054457e51A196144C78F",
    }
}

CHAINLINK_AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    }
]

def get_stablecoin_price(w3, symbol, chain="arbitrum"):
    """
    Récupère le prix d'un stablecoin depuis Chainlink, DefiLlama, ou la simulation.
    """
    if symbol in MOCK_PRICES:
        return MOCK_PRICES[symbol]
        
    if w3 and chain in CHAINLINK_FEEDS and symbol in CHAINLINK_FEEDS[chain]:
        try:
            feed_addr = CHAINLINK_FEEDS[chain][symbol]
            feed_contract = w3.eth.contract(address=Web3.to_checksum_address(feed_addr), abi=CHAINLINK_AGGREGATOR_ABI)
            latest_data = feed_contract.functions.latestRoundData().call()
            decimals = feed_contract.functions.decimals().call()
            price = latest_data[1] / (10 ** decimals)
            print(f"[ORACLE] Prix Chainlink pour {symbol} sur {chain} : {price:.4f}$")
            return price
        except Exception as e:
            print(f"[ORACLE] Échec Chainlink pour {symbol} ({e}). Utilisation du fallback DefiLlama.", file=sys.stderr)
            
    cg_id = COINGECKO_IDS.get(symbol.upper())
    if cg_id:
        try:
            url = f"https://coins.llama.fi/prices/current/coingecko:{cg_id}"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                price = data.get("coins", {}).get(f"coingecko:{cg_id}", {}).get("price")
                if price is not None:
                    print(f"[ORACLE] Prix DefiLlama pour {symbol} : {price:.4f}$")
                    return price
        except Exception as e:
            print(f"[ORACLE] Échec DefiLlama pour {symbol}: {e}", file=sys.stderr)
            
    print(f"[ORACLE] Prix par défaut (1.00$) pour {symbol}")
    return 1.0

def trigger_withdrawal_flow(w3, vault_contract, shares, hot_wallet, private_key=None, cold_wallet=None, max_slippage_pct=2.0):
    """
    Gère le flux de retrait des parts, avec support de la ségrégation Hot/Cold wallet.
    """
    hot_addr = Web3.to_checksum_address(hot_wallet)
    
    if not cold_wallet:
        print(f"[RETRAIT] Lancement du retrait direct de {shares / (10**18):.4f} parts.")
        withdraw_call = vault_contract.functions.withdraw(shares)
        if w3:
            send_transaction_with_safety(w3, withdraw_call, hot_addr, private_key)
        else:
            print(f"[Simulation] withdraw(shares={shares})")
        return "WITHDRAWN"
        
    cold_addr = Web3.to_checksum_address(cold_wallet)
    print(f"[RETRAIT COLD] Démarrage du flux sécurisé Hot/Cold Wallet.")
    print(f"  Hot Wallet (Exécuteur transaction) : {hot_addr}")
    print(f"  Cold Wallet (Propriétaire des parts) : {cold_addr}")
    
    try:
        hot_shares = vault_contract.functions.balanceOf(hot_addr).call()
    except Exception:
        hot_shares = 0
        
    if hot_shares < shares:
        needed = shares - hot_shares
        print(f"[RETRAIT COLD] Le Hot Wallet ne possède pas assez de parts ({hot_shares/(10**18):.2f} < {shares/(10**18):.2f}).")
        print(f"[RETRAIT COLD] Récupération de {needed/(10**18):.4f} parts depuis le Cold Wallet...")
        
        try:
            allowance = vault_contract.functions.allowance(cold_addr, hot_addr).call()
        except Exception:
            allowance = 0
            
        if allowance < needed:
            msg = f"[ERREUR COLD] Le Cold Wallet n'a pas approuvé le Hot Wallet pour gérer les mooTokens (Allowance: {allowance/(10**18):.4f} < Requis: {needed/(10**18):.4f}). Action annulée."
            print(msg, file=sys.stderr)
            raise PermissionError(msg)
            
        transfer_from_call = vault_contract.functions.transferFrom(cold_addr, hot_addr, needed)
        if w3:
            send_transaction_with_safety(w3, transfer_from_call, hot_addr, private_key)
        else:
            print(f"[Simulation] transferFrom(from={cold_addr}, to={hot_addr}, value={needed})")
            
    # Étape 2 : Retrait
    print(f"[RETRAIT COLD] Retrait de {shares / (10**18):.4f} parts...")
    withdraw_call = vault_contract.functions.withdraw(shares)
    if w3:
        send_transaction_with_safety(w3, withdraw_call, hot_addr, private_key)
    else:
        print(f"[Simulation] withdraw(shares={shares})")
        
    # Étape 3 : Rapatrier les fonds vers le Cold Wallet
    try:
        want_token_address = vault_contract.functions.want().call()
        want_contract = w3.eth.contract(address=want_token_address, abi=ERC20_ABI)
        want_symbol = want_contract.functions.symbol().call()
        want_decimals = want_contract.functions.decimals().call()
        if w3:
            hot_want_balance = want_contract.functions.balanceOf(hot_addr).call()
        else:
            hot_want_balance = int((shares / (10**18)) * 1.0 * (10 ** want_decimals))
    except Exception:
        want_symbol = "USDC"
        want_decimals = 6
        hot_want_balance = int(shares / (10**18) * (10**6))
        want_contract = None
        
    print(f"[RETRAIT COLD] Transfert de {hot_want_balance / (10 ** want_decimals):.2f} {want_symbol} vers le Cold Wallet...")
    if w3 and want_contract:
        transfer_back_call = want_contract.functions.transfer(cold_addr, hot_want_balance)
        send_transaction_with_safety(w3, transfer_back_call, hot_addr, private_key)
    else:
        print(f"[Simulation] transfer(to={cold_addr}, value={hot_want_balance}) sur {want_symbol}")
        
    print("[RETRAIT COLD] Flux sécurisé Hot/Cold Wallet terminé avec succès.")
    return "WITHDRAWN"

def check_depeg_and_execute_strategy(w3, vault_address, wallet_address, private_key=None, cold_wallet=None):
    """
    Arbre de décision à 3 niveaux face au depeg de stablecoins.
    """
    print("\n=== Lancement de la vérification de peg ===")
    v_addr = Web3.to_checksum_address(vault_address)
    vault_contract = w3.eth.contract(address=v_addr, abi=BEEFY_VAULT_ABI) if w3 else None
    
    chain = "arbitrum"
    if w3:
        try:
            chain_id = w3.eth.chain_id
            if chain_id == 8453:
                chain = "base"
            elif chain_id == 10:
                chain = "optimism"
            elif chain_id == 137:
                chain = "polygon"
        except Exception:
            pass
            
    try:
        want_token_address = vault_contract.functions.want().call()
        want_contract = w3.eth.contract(address=want_token_address, abi=ERC20_ABI)
        want_symbol = want_contract.functions.symbol().call()
    except Exception:
        want_symbol = "USDC"
        
    clean_sym = want_symbol.replace("LP", "").replace("moo", "").strip()
    assets = [clean_sym]
    for sep in ["-", "_", "/"]:
        if sep in clean_sym:
            assets = [a.strip() for a in clean_sym.split(sep)]
            break
            
    print(f"Actifs sous-jacents détectés : {assets}")
    prices = {}
    for asset in assets:
        prices[asset] = get_stablecoin_price(w3, asset, chain=chain)
        
    min_price = min(prices.values())
    depegged_asset = min(prices, key=prices.get)
    print(f"Prix le plus bas constaté : {min_price:.4f}$ ({depegged_asset})")
    
    if min_price >= 0.99:
        print(f"[DECISION] Pég stable ou Micro-depeg ({min_price:.4f}$ >= 0.99$). Action: HOLD (Maintien de la position).")
        return "HOLD"
        
    elif 0.95 <= min_price < 0.99:
        print(f"[DECISION] Depeg MODÉRÉ détecté ({min_price:.4f}$ pour {depegged_asset}). Simulation du retrait via eth_call...")
        
        target_holder = cold_wallet if cold_wallet else wallet_address
        try:
            user_shares = vault_contract.functions.balanceOf(Web3.to_checksum_address(target_holder)).call()
        except Exception:
            user_shares = 100 * (10 ** 18)
            
        if user_shares == 0:
            user_shares = 100 * (10 ** 18)
            
        # Simuler le retrait
        try:
            try:
                want_decimals = want_contract.functions.decimals().call()
            except Exception:
                want_decimals = 6
                
            redeemable_want = vault_contract.functions.previewRedeem(user_shares).call()
            price_multiplier = vault_contract.functions.getPricePerFullShare().call() / (10**18)
            
            theoretical_want_normalized = (user_shares / (10**18)) * price_multiplier
            redeemable_want_normalized = redeemable_want / (10 ** want_decimals)
            
            if theoretical_want_normalized > 0:
                slippage = 1.0 - (redeemable_want_normalized / theoretical_want_normalized)
            else:
                slippage = 0.0
            print(f"[SIMULATION] Reçu théorique : {theoretical_want_normalized:.2f} | Simulé : {redeemable_want_normalized:.2f} | Slippage estimé : {slippage*100:.2f}%")
        except Exception as e:
            slippage = (1.0 - min_price) * 0.5
            print(f"[SIMULATION] Impossible de simuler previewRedeem ({e}). Slippage simulé théorique : {slippage*100:.2f}%")
            
        if slippage <= 0.02:
            print(f"[DECISION] Slippage effectif ({slippage*100:.2f}%) <= 2%. Retrait sécurisé. Exécution de la liquidation progressive...")
            return trigger_withdrawal_flow(w3, vault_contract, user_shares, wallet_address, private_key, cold_wallet, max_slippage_pct=2.0)
        else:
            print(f"[DECISION] Slippage effectif ({slippage*100:.2f}%) > 2%. Annulation du retrait pour éviter l'arbitrage MEV.")
            return "HOLD"
            
    else: # min_price < 0.95
        print(f"[DECISION] DEPEG CRITIQUE DÉTECTÉ ({min_price:.4f}$ < 0.95$). Retrait d'urgence avec tolérance élevée au slippage (40%).")
        target_holder = cold_wallet if cold_wallet else wallet_address
        try:
            user_shares = vault_contract.functions.balanceOf(Web3.to_checksum_address(target_holder)).call()
        except Exception:
            user_shares = 100 * (10 ** 18)
            
        if user_shares == 0:
            user_shares = 100 * (10 ** 18)
            
        if w3 and vault_contract:
            return trigger_withdrawal_flow(w3, vault_contract, user_shares, wallet_address, private_key, cold_wallet, max_slippage_pct=40.0)
        else:
            # En mode simulation pure
            return trigger_withdrawal_flow(None, None, user_shares, wallet_address, private_key, cold_wallet, max_slippage_pct=40.0)

def query_vault_info(w3, vault_address, wallet_address):
    """
    Récupère les informations réelles d'un vault Beefy à l'aide de Web3.
    """
    try:
        v_addr = Web3.to_checksum_address(vault_address)
        vault_contract = w3.eth.contract(address=v_addr, abi=BEEFY_VAULT_ABI)
        
        want_token_address = vault_contract.functions.want().call()
        want_contract = w3.eth.contract(address=want_token_address, abi=ERC20_ABI)
        
        decimals = want_contract.functions.decimals().call()
        symbol = want_contract.functions.symbol().call()
        
        price_per_share = vault_contract.functions.getPricePerFullShare().call()
        price_multiplier = price_per_share / (10 ** 18)
        
        total_vault_balance = vault_contract.functions.balance().call() / (10 ** decimals)
        
        user_moo_balance = 0
        user_want_balance = 0
        user_allowance = 0
        
        if wallet_address and wallet_address != "0x0000000000000000000000000000000000000000":
            w_addr = Web3.to_checksum_address(wallet_address)
            user_moo_balance = vault_contract.functions.balanceOf(w_addr).call()
            user_want_balance = want_contract.functions.balanceOf(w_addr).call()
            user_allowance = want_contract.functions.allowance(w_addr, v_addr).call()
            
        user_moo_balance_decimal = user_moo_balance / (10 ** 18)
        user_underlying_value = user_moo_balance_decimal * price_multiplier
        
        return {
            "connected": True,
            "vault_address": v_addr,
            "want_address": want_token_address,
            "want_symbol": symbol,
            "want_decimals": decimals,
            "price_per_share": price_multiplier,
            "total_vault_assets": total_vault_balance,
            "user_moo_balance": user_moo_balance_decimal,
            "user_want_balance": user_want_balance / (10 ** decimals),
            "user_underlying_value": user_underlying_value,
            "user_allowance": user_allowance / (10 ** decimals),
            "raw_user_want_balance": user_want_balance,
            "raw_allowance": user_allowance
        }
    except Exception as e:
        print(f"Erreur lors de la lecture on-chain : {e}", file=sys.stderr)
        return {"connected": False, "error": str(e)}

def run_simulation(vault_address, wallet_address):
    """
    Simule les données du vault pour la démonstration si aucun RPC n'est configuré.
    """
    return {
        "connected": False,
        "vault_address": vault_address,
        "want_address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "want_symbol": "USDC",
        "want_decimals": 6,
        "price_per_share": 1.05432,
        "total_vault_assets": 1254320.50,
        "user_moo_balance": 150.0,
        "user_want_balance": 500.0,
        "user_underlying_value": 150.0 * 1.05432,
        "user_allowance": 0.0,
        "raw_user_want_balance": 500 * (10 ** 6),
        "raw_allowance": 0
    }

def print_report(data):
    """
    Affiche un résumé structuré des informations du vault.
    """
    print("\n=== INFORMATIONS DU VAULT DEFI ===")
    if not data.get("connected"):
        print("[!] MODE SIMULATION (Aucune connexion RPC configurée)")
        
    print(f"Adresse du Vault : {data['vault_address']}")
    print(f"Token sous-jacent: {data['want_symbol']} ({data['want_address']})")
    print(f"Actifs totaux sous gestion : {data['total_vault_assets']:,.2f} {data['want_symbol']}")
    print(f"Taux multiplicateur (Price per Share) : {data['price_per_share']:.6f} {data['want_symbol']}/mooToken")
    print("-" * 50)
    print("=== SOLDE ET PORTEFEUILLE UTILISATEUR ===")
    print(f"Solde dans le portefeuille : {data['user_want_balance']:.2f} {data['want_symbol']}")
    print(f"Solde de parts (mooTokens) : {data['user_moo_balance']:.6f} moo{data['want_symbol']}")
    print(f"Valeur équivalente sous-jacente : {data['user_underlying_value']:.4f} {data['want_symbol']}")
    print(f"Autorisation de dépense (Allowance) : {data['user_allowance']:.2f} {data['want_symbol']}")
    print("-" * 50)

def prepare_deposit(w3, data, amount_to_deposit):
    """
    Simule ou prépare les transactions de dépôt.
    """
    print(f"\n--- PRÉPARATION DU DÉPÔT : {amount_to_deposit} {data['want_symbol']} ---")
    raw_amount = int(amount_to_deposit * (10 ** data['want_decimals']))
    
    if data['user_want_balance'] < amount_to_deposit:
        print(f"[ERREUR] Solde insuffisant. Vous avez {data['user_want_balance']} {data['want_symbol']} et tentez de déposer {amount_to_deposit}.")
        return
        
    if data['user_allowance'] < amount_to_deposit:
        print(f"[1/2] L'autorisation de dépense est insuffisante ({data['user_allowance']} < {amount_to_deposit}).")
        print(">> Transaction d'approbation requise (ERC20 approve) :")
        print(f"   [Simulation] approve(spender={data['vault_address']}, value={raw_amount}) sur {data['want_address']}")
    else:
        print("[1/2] L'autorisation de dépense (Allowance) est suffisante. Pas d'approbation requise.")
        
    print(">> Transaction de dépôt requise (Beefy deposit) :")
    print(f"   [Simulation] deposit(amount={raw_amount}) sur le vault {data['vault_address']}")

def prepare_withdrawal(w3, data, shares_to_withdraw):
    """
    Simule ou prépare les transactions de retrait.
    """
    print(f"\n--- PRÉPARATION DU RETRAIT : {shares_to_withdraw} moo{data['want_symbol']} ---")
    raw_shares = int(shares_to_withdraw * (10 ** 18))
    
    if data['user_moo_balance'] < shares_to_withdraw:
        print(f"[ERREUR] Solde de parts insuffisant. Vous possédez {data['user_moo_balance']} mooTokens et tentez de retirer {shares_to_withdraw}.")
        return
        
    print(">> Transaction de retrait requise (Beefy withdraw) :")
    estimated_underlying = shares_to_withdraw * data['price_per_share']
    print(f"   [Simulation] withdraw(shares={raw_shares}) sur le vault {data['vault_address']}")
    print(f"   [Simulation] Vous récupérerez environ : {estimated_underlying:.4f} {data['want_symbol']}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Bot DeFi Stablecoin - Execution & Protection")
    parser.add_argument("--vault", type=str, default=None, help="Adresse du vault Beefy Finance")
    parser.add_argument("--check-peg", action="store_true", help="Vérifie le peg et exécute la stratégie de protection")
    parser.add_argument("--simulate-depeg", type=float, default=None, help="Simule un prix de stablecoin pour le test (ex: 0.97)")
    parser.add_argument("--deposit", type=float, default=None, help="Prépare et simule un dépôt du montant spécifié")
    parser.add_argument("--withdraw", type=float, default=None, help="Prépare et simule un retrait de parts")
    
    args = parser.parse_args()
    
    vault_address = args.vault or os.getenv("DEFI_VAULT_ADDRESS", "0x0000000000000000000000000000000000000000")
    if vault_address == "0x0000000000000000000000000000000000000000":
        # Silo USDC sur Arbitrum One par défaut
        vault_address = "0x83152eE78d8f20Bba134A5FF000D551355Ce3996"
        
    wallet_address = os.getenv("USER_WALLET_ADDRESS", "0x0000000000000000000000000000000000000000")
    hot_wallet = os.getenv("HOT_WALLET_ADDRESS") or wallet_address
    private_key = os.getenv("HOT_WALLET_PRIVATE_KEY")
    cold_wallet = os.getenv("COLD_WALLET_ADDRESS")
    
    # Validation OpSec : bloquer le démarrage si la clé privée est absente en production (mode non-simulation)
    simulation_mode = os.getenv("SIMULATION_MODE", "false").lower() in ("true", "1", "yes")
    is_simulation = (args.simulate_depeg is not None) or simulation_mode
    is_real_action = args.check_peg or (args.deposit is not None) or (args.withdraw is not None)
    
    if is_real_action and not is_simulation and not private_key:
        raise ValueError("[OpSec ERROR] HOT_WALLET_PRIVATE_KEY est absente. Impossible d'exécuter des transactions réelles sans clé privée.")
    
    if args.simulate_depeg is not None:
        MOCK_PRICES["USDC"] = args.simulate_depeg
        MOCK_PRICES["USDT"] = args.simulate_depeg
        MOCK_PRICES["DAI"] = args.simulate_depeg
        print(f"[TEST] Simulation d'un prix de stablecoin à {args.simulate_depeg}$")
        
    print("Initialisation du gestionnaire de RPC...")
    rpc_manager = RPCManager()
    w3 = rpc_manager.get_w3()
    
    if w3:
        print("Connexion active. Lecture des informations du vault...")
        data = execute_with_retry(rpc_manager, query_vault_info, vault_address, cold_wallet if cold_wallet else hot_wallet)
    else:
        print("Aucune connexion RPC. Mode simulation.")
        data = run_simulation(vault_address, cold_wallet if cold_wallet else hot_wallet)
        
    if "error" in data:
        print(f"Impossible de récupérer les informations du vault: {data['error']}", file=sys.stderr)
        sys.exit(1)
        
    print_report(data)
    
    if args.check_peg or args.simulate_depeg is not None:
        execute_with_retry(
            rpc_manager,
            check_depeg_and_execute_strategy,
            vault_address,
            hot_wallet,
            private_key=private_key,
            cold_wallet=cold_wallet
        )
    elif args.deposit is not None:
        v_addr = Web3.to_checksum_address(vault_address)
        vault_contract = w3.eth.contract(address=v_addr, abi=BEEFY_VAULT_ABI) if w3 else None
        want_token_address = data["want_address"]
        want_contract = w3.eth.contract(address=want_token_address, abi=ERC20_ABI) if w3 else None
        
        raw_amount = int(args.deposit * (10 ** data['want_decimals']))
        
        if data['user_allowance'] < args.deposit:
            print(f"Autorisation de dépense insuffisante ({data['user_allowance']} < {args.deposit}).")
            if w3 and want_contract:
                approve_call = want_contract.functions.approve(v_addr, raw_amount)
                execute_with_retry(rpc_manager, send_transaction_with_safety, approve_call, hot_wallet, private_key)
            else:
                print(f"[Simulation] approve(spender={v_addr}, value={raw_amount})")
        else:
            print("Allowance suffisante.")
            
        if w3 and vault_contract:
            deposit_call = vault_contract.functions.deposit(raw_amount)
            execute_with_retry(rpc_manager, send_transaction_with_safety, deposit_call, hot_wallet, private_key)
        else:
            print(f"[Simulation] deposit(amount={raw_amount})")
            
    elif args.withdraw is not None:
        v_addr = Web3.to_checksum_address(vault_address)
        vault_contract = w3.eth.contract(address=v_addr, abi=BEEFY_VAULT_ABI) if w3 else None
        raw_shares = int(args.withdraw * (10 ** 18))
        
        execute_with_retry(
            rpc_manager,
            trigger_withdrawal_flow,
            vault_contract,
            raw_shares,
            hot_wallet,
            private_key=private_key,
            cold_wallet=cold_wallet
        )
    else:
        print("\n--- MODE DÉMO PAR DÉFAUT ---")
        prepare_deposit(w3, data, 100.0)
        prepare_withdrawal(w3, data, 50.0)

if __name__ == "__main__":
    main()
