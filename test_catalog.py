from market_data import MarketDataCatalog
catalog = MarketDataCatalog()
print(catalog._resolve_path('BTC-USD'))
print(catalog.get_first_available_date('BTC-USD'))
