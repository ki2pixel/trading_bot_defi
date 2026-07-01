#!/usr/bin/env python3
"""
Bot DeFi Stablecoin - Vault Finder
Ce script interroge l'API publique de Beefy Finance pour identifier, filtrer et classer
les meilleurs vaults de stablecoins sur les réseaux L2 (Arbitrum, Base, Optimism, Polygon).
"""

import argparse
import json
import requests
import sys

# Liste exhaustive des tickers de stablecoins (USD, EUR, etc.)
STABLECOINS = {
    # USD Stables
    "USDT", "USDC", "DAI", "LUSD", "MAI", "FRAX", "MIM", "USDE", "SUSDE", 
    "FDUSD", "TUSD", "USDC.E", "USDT.E", "USDV", "USD+", "USDT+", "USDbC", 
    "DAI.E", "AUSD", "ZUSD", "GUSD", "BUSD", "PYUSD", "USDY", "GHO", "BOB", "USDF",
    # EUR Stables
    "EUR", "EURA", "EURE", "EUR+", "AEUR", "JEUR"
}

# Réseaux L2 supportés par défaut pour minimiser les frais de transaction
L2_CHAINS = {"arbitrum", "base", "optimism", "polygon"}

def fetch_beefy_data():
    """
    Récupère les informations des vaults et les APY depuis l'API de Beefy Finance.
    """
    print("Fetching vaults from Beefy Finance...")
    vaults_res = requests.get("https://api.beefy.finance/vaults", timeout=15)
    vaults_res.raise_for_status()
    vaults = vaults_res.json()

    print("Fetching APYs from Beefy Finance...")
    apy_res = requests.get("https://api.beefy.finance/apy", timeout=15)
    apy_res.raise_for_status()
    apys = apy_res.json()

    return vaults, apys

def is_stable_vault(vault):
    """
    Vérifie si un vault contient uniquement des stablecoins dans ses actifs.
    """
    assets = vault.get("assets", [])
    if not assets:
        return False
    
    # Pour chaque actif dans le pool, on vérifie s'il s'agit d'un stablecoin connu
    for asset in assets:
        asset_upper = asset.upper()
        # Supprime les suffixes communs comme .E (Bridge tokens sur Avalanche/Arbitrum)
        # ou les préfixes de wrapper comme 'W' (WUSDR -> USDR)
        clean_asset = asset_upper.replace(".E", "").replace("WUSDC", "USDC").replace("WUSDT", "USDT")
        
        # Test direct et test avec préfixe/suffixe nettoyé
        if asset_upper not in STABLECOINS and clean_asset not in STABLECOINS:
            # Si un seul token du pool n'est pas stable (ex: ETH, BTC), le vault n'est pas stable
            return False
            
    return True

def calculate_break_even(current_apy, new_apy, capital, days, zap_in, zap_out, withdraw, slippage):
    """
    Calcule si migrer vers un nouveau vault est rentable après prise en compte des frais.
    Retourne (is_profitable, net_profit, break_even_apy, total_friction_pct)
    """
    # Conversion des pourcentages en décimales (ex: 0.05% -> 0.0005)
    f_zap_in = zap_in / 100.0
    f_zap_out = zap_out / 100.0
    f_withdraw = withdraw / 100.0
    f_slippage = slippage / 100.0
    
    # Friction totale (Entrée et sortie : Zap In + Zap Out + Retrait + 2x Slippage)
    total_friction_rate = f_zap_in + f_zap_out + f_withdraw + (2 * f_slippage)
    
    # Valeur finale du capital si on reste sur le vault actuel
    val_current = capital * ((1 + current_apy) ** (days / 365.0))
    
    # Valeur finale du capital si on migre vers le nouveau vault (après friction initiale)
    remaining_capital = capital * (1 - total_friction_rate)
    val_new = remaining_capital * ((1 + new_apy) ** (days / 365.0))
    
    net_profit = val_new - val_current
    is_profitable = net_profit > 0
    
    # APY minimum requis pour break-even
    if total_friction_rate < 1.0 and days > 0:
        exponent = 365.0 / days
        try:
            break_even_apy = ((1.0 + current_apy) / ((1.0 - total_friction_rate) ** exponent)) - 1.0
        except OverflowError:
            break_even_apy = float('inf')
    else:
        break_even_apy = float('inf')
        
    return is_profitable, net_profit, break_even_apy, total_friction_rate * 100.0

def filter_and_rank_vaults(vaults, apys, chains=None, min_apy=0.0):
    """
    Filtre les vaults actifs selon les critères (L2, stablecoins) et les classe par APY.
    """
    if chains is None:
        chains = L2_CHAINS
        
    filtered = []
    
    for vault in vaults:
        # Critères de base
        if vault.get("status") != "active":
            continue
            
        chain = vault.get("chain")
        if chain not in chains:
            continue
            
        # Doit être un vault standard de stablecoins
        if not is_stable_vault(vault):
            continue
            
        vault_id = vault.get("id")
        # Récupération de l'APY
        apy = apys.get(vault_id, 0.0)
        
        # Beefy renvoie parfois None ou des formats invalides pour l'APY
        if apy is None:
            apy = 0.0
            
        # Filtrage par APY minimum
        if apy < min_apy:
            continue
            
        filtered.append({
            "id": vault_id,
            "name": vault.get("name"),
            "chain": chain,
            "platform": vault.get("platformId", "unknown"),
            "apy": apy,
            "assets": vault.get("assets"),
            "vaultAddress": vault.get("earnContractAddress"),
            "tokenAddress": vault.get("tokenAddress"),
        })
        
    # Classement par APY décroissant
    filtered.sort(key=lambda x: x["apy"], reverse=True)
    return filtered

def display_report(vaults_list, limit=10, current_apy=None, capital=10000.0, amortization_days=30):
    """
    Affiche le rapport des vaults sous forme de tableau texte dans la console.
    """
    if not vaults_list:
        print("\nAucun vault stablecoin ne correspond aux critères spécifiés.")
        return
        
    if current_apy is not None:
        print(f"\n=== COMPARAISON ET ANALYSE DE RENTABILITÉ (BREAK-EVEN) ===")
        print(f"Capital de départ : {capital:,.2f} USD")
        print(f"APY du vault actuel : {current_apy * 100:.2f}%")
        print(f"Période d'amortissement ciblée : {amortization_days} jours")
        print(f"Frais de friction simulés : Zap In, Zap Out, Retrait et Slippage pris en compte.")
        print("=" * 135)
        print(f"{'Rang':<5} | {'Nom du Vault':<22} | {'Réseau':<10} | {'APY (%)':<8} | {'Friction (%)':<12} | {'APY Net (%)':<11} | {'Profit net ($)':<14} | {'Rentable?':<9} | {'Adresse Contract'}")
        print("-" * 135)
        
        for i, vault in enumerate(vaults_list[:limit], 1):
            apy_percent = vault["apy"] * 100
            be = vault.get("break_even", {})
            
            friction_str = f"{be.get('total_friction', 0.0):.2f}%"
            friction_rate = be.get('total_friction', 0.0) / 100.0
            
            # Formule APY Net annualisé
            net_apy_val = ((1.0 - friction_rate) ** (365.0 / amortization_days)) * (1.0 + vault["apy"]) - 1.0
            net_apy_percent = net_apy_val * 100
            
            profit_str = f"{be.get('net_profit', 0.0):+.2f}$"
            rentable_str = "🟢 OUI" if be.get('is_profitable', False) else "🔴 NON"
            
            row = (
                f"{i:<5} | "
                f"{vault['name'][:22]:<22} | "
                f"{vault['chain']:<10} | "
                f"{apy_percent:>7.2f}% | "
                f"{friction_str:>12} | "
                f"{net_apy_percent:>10.2f}% | "
                f"{profit_str:>14} | "
                f"{rentable_str:<9} | "
                f"{vault['vaultAddress']}"
            )
            print(row)
        print("-" * 135)
    else:
        print(f"\n=== TOP {limit} DES MEILLEURS VAULTS DE STABLECOINS SUR L2 (BEEFY FINANCE) ===")
        print("-" * 115)
        header = f"{'Rang':<5} | {'Nom du Vault':<25} | {'Réseau':<10} | {'DEX/Plateforme':<15} | {'APY (%)':<10} | {'Actifs':<15} | {'Adresse Contract'}"
        print(header)
        print("-" * 115)
        
        for i, vault in enumerate(vaults_list[:limit], 1):
            apy_percent = vault["apy"] * 100
            assets_str = "-".join(vault["assets"])
            row = (
                f"{i:<5} | "
                f"{vault['name'][:25]:<25} | "
                f"{vault['chain']:<10} | "
                f"{vault['platform'][:15]:<15} | "
                f"{apy_percent:>8.2f}% | "
                f"{assets_str[:15]:<15} | "
                f"{vault['vaultAddress']}"
            )
            print(row)
        print("-" * 115)

def main():
    parser = argparse.ArgumentParser(description="Recherche et classe les vaults de stablecoins sur Beefy Finance.")
    parser.add_argument("--chains", nargs="+", default=list(L2_CHAINS), help="Liste des chaînes à analyser (ex: arbitrum base)")
    parser.add_argument("--min-apy", type=float, default=1.0, help="APY minimum en pourcentage (ex: 5.0 pour 5%)")
    parser.add_argument("--limit", type=int, default=10, help="Nombre maximal de résultats à afficher")
    parser.add_argument("--output", type=str, default=None, help="Chemin vers un fichier JSON pour sauvegarder les résultats")
    
    # Arguments pour le calcul de break-even
    parser.add_argument("--current-vault", type=str, default=None, help="ID du vault actuellement occupé (ex: curve-usdc-usdt)")
    parser.add_argument("--current-apy", type=float, default=None, help="APY du vault actuel en % (ex: 5.0 pour 5%)")
    parser.add_argument("--capital", type=float, default=10000.0, help="Capital total déployé en USD (défaut: 10000.0)")
    parser.add_argument("--amortization-days", type=int, default=30, help="Nombre de jours pour le calcul de break-even (défaut: 30)")
    parser.add_argument("--zap-in-fee", type=float, default=0.05, help="Frais de Zap à l'entrée en % (défaut: 0.05%)")
    parser.add_argument("--zap-out-fee", type=float, default=0.05, help="Frais de Zap à la sortie en % (défaut: 0.05%)")
    parser.add_argument("--withdrawal-fee", type=float, default=0.05, help="Frais de retrait en % (défaut: 0.05%)")
    parser.add_argument("--slippage", type=float, default=0.1, help="Slippage de swap/conversion en % (défaut: 0.1%)")
    
    args = parser.parse_args()
    
    # Conversion du paramètre APY en float décimal (ex: 5.0% -> 0.05)
    min_apy_decimal = args.min_apy / 100.0
    chains_set = set(c.lower() for c in args.chains)
    
    try:
        vaults, apys = fetch_beefy_data()
    except Exception as e:
        print(f"Error fetching data from Beefy API: {e}", file=sys.stderr)
        sys.exit(1)
    ranked_vaults = filter_and_rank_vaults(vaults, apys, chains=chains_set, min_apy=min_apy_decimal)
    
    # Logique d'analyse de break-even
    current_apy_val = None
    if args.current_vault:
        # Recherche de l'APY dans les données de Beefy
        current_apy_val = apys.get(args.current_vault)
        if current_apy_val is not None:
            print(f"\n[INFO] Vault actuel '{args.current_vault}' trouvé avec un APY de {current_apy_val * 100:.2f}%")
        else:
            print(f"\n[WARNING] Le vault actuel '{args.current_vault}' n'a pas été trouvé dans l'API de Beefy.")
            
    if current_apy_val is None and args.current_apy is not None:
        current_apy_val = args.current_apy / 100.0
        
    if current_apy_val is not None:
        # Enrichir la liste avec les données de break-even
        for vault in ranked_vaults:
            is_profitable, net_profit, break_even_apy, total_friction = calculate_break_even(
                current_apy_val,
                vault["apy"],
                args.capital,
                args.amortization_days,
                args.zap_in_fee,
                args.zap_out_fee,
                args.withdrawal_fee,
                args.slippage
            )
            vault["break_even"] = {
                "is_profitable": is_profitable,
                "net_profit": net_profit,
                "break_even_apy": break_even_apy,
                "total_friction": total_friction
            }
            
    display_report(
        ranked_vaults,
        limit=args.limit,
        current_apy=current_apy_val,
        capital=args.capital,
        amortization_days=args.amortization_days
    )
    
    if args.output:
        try:
            with open(args.output, "w") as f:
                json.dump(ranked_vaults[:args.limit], f, indent=4)
            print(f"\nRésultats sauvegardés dans {args.output}")
        except OSError as e:
            print(f"Erreur lors de l'écriture du fichier de sortie : {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
