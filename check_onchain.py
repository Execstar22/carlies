import sys
sys.stdout.reconfigure(encoding='utf-8')
from web3 import Web3

w3 = Web3(Web3.HTTPProvider('https://polygon-mainnet.infura.io/v3/ef1c20203b154db9ae7091c4de4d55fc'))
proxy = '0x1ce5685dbd3576102317F8C6958AecddECdEAC60'

tokens = {
    'pUSD': '0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB',
    'USDC': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    'USDC.e': '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
}

exchanges = {
    'exchange_v2': '0xE111180000d2663C0091e4f400237545B87B996B',
    'mystery_ex': '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296',
    'neg_risk_v2': '0xe2222d279d744050d28e00520010520000310F59',
}

abi_balance = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]
abi_allowance = [{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]

print("=== PROXY WALLET BALANCES ===")
for name, addr in tokens.items():
    c = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=abi_balance)
    bal = c.functions.balanceOf(Web3.to_checksum_address(proxy)).call() / 1e6
    print(f"  {name}: ${bal:.6f}")

print("\n=== ON-CHAIN ALLOWANCES (proxy -> exchange) ===")
for tname, taddr in tokens.items():
    c = w3.eth.contract(address=Web3.to_checksum_address(taddr), abi=abi_allowance)
    for ename, eaddr in exchanges.items():
        allow = c.functions.allowance(
            Web3.to_checksum_address(proxy),
            Web3.to_checksum_address(eaddr)
        ).call() / 1e6
        if allow > 0:
            print(f"  {tname} -> {ename}: ${allow:.2f} *** HAS ALLOWANCE ***")
        else:
            print(f"  {tname} -> {ename}: $0")

# Also check what collateral the exchange_v2 uses
print("\n=== EXCHANGE COLLATERAL CHECK ===")
for ename, eaddr in exchanges.items():
    try:
        # Try calling getCollateral() or collateral()
        result = w3.eth.call({"to": Web3.to_checksum_address(eaddr), "data": "0xd8dfeb45"})  # getCollateral()
        collateral = "0x" + result.hex()[-40:]
        match = "UNKNOWN"
        for tname, taddr in tokens.items():
            if collateral.lower() == taddr.lower():
                match = tname
        print(f"  {ename} collateral: {collateral} ({match})")
    except:
        try:
            result = w3.eth.call({"to": Web3.to_checksum_address(eaddr), "data": "0xe8b5e51f"})  # collateral()
            collateral = "0x" + result.hex()[-40:]
            match = "UNKNOWN"
            for tname, taddr in tokens.items():
                if collateral.lower() == taddr.lower():
                    match = tname
            print(f"  {ename} collateral: {collateral} ({match})")
        except:
            print(f"  {ename}: could not query collateral")
