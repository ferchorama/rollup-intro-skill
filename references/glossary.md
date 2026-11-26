# Caption glossary — crypto names and tickers

Proofread every on-screen word against this list before shipping. Tickers are written in
**capitals**. Names keep their usual capitalisation. Script / chyron / this glossary beat
ASR (RULE 047).

Check a script or words file with:

```
python scripts/glossary.py words_C.json
python scripts/glossary.py --text "I need everything soul"
```

The checker only reports. It never rewrites a file.

## Tickers

| Say / write | Not | Notes |
|---|---|---|
| SOL | soul, sol, sold, sohl | Solana's ticker. Kash Dhanda C_v7 showed "soul" on screen. |
| ETH | eth, ether (when used as the ticker) | Ethereum's ticker |
| BTC | btc, bitcoin (when used as the ticker) | Bitcoin's ticker |
| USDC | usdc, U.S.D.C. | |
| USDT | usdt | |
| USD | usd | |
| L2 | l2, layer two (when used as the ticker-style label) | Layer 2 |

## Names and common terms

| Write | Not | Notes |
|---|---|---|
| Solana | solana, Salana, Solanna | |
| Ethereum | ethereum, etherium, Ethereom | |
| Bitcoin | bitcoin (as the name, mid-sentence is fine), bit coin | |
| DeFi | defi, De-Fi, defy | |
| NFT | nft, N.F.T. | |
| DAO | dao | |
| Layer 2 | layer 2, layer-2 are fine; not "later 2" | |
| staking | sticking (ASR) | |
| validator | validater | |
| mainnet | main net, Main Net | |
| testnet | test net | |
| airdrop | air drop, air-drop | |
| rollup | roll up, Roll Up (the show name is **The Rollup**) | |
| The Rollup | The Roll Up, the rollup | Show name |

## ASR confusions to listen for

These are easy to miss because they sound right:

- **soul / sol / sold** when the guest is talking about Solana → **SOL**
- **ether / either / ethereum** when they said the ticker → **ETH**
- **bit / beat / bee tee see** when they said the ticker → **BTC**
- Proper nouns and protocol names: confirm against the chyron, slides, or the guest's usual spelling (rule in SKILL.md: DTCC, not "DTC")

When a caption change is only a spelling fix (SOL for soul), keep the picture and timing and ship a new `_vN`.
