"""Offline provider contract checks. Uses no real credentials or network."""
from pathlib import Path
from unittest.mock import patch, Mock
from game_autofill import search_games, game_details, allowed_bgg_cover_url

result=search_games('Крила','')
assert result['provider_error']=='bgg_not_configured' and not result['candidates']
assert 'youtube.com/results?' in result['search_links']['youtube']
assert not allowed_bgg_cover_url('http://cf.geekdo-images.com/cover.jpg')
assert not allowed_bgg_cover_url('https://cf.geekdo-images.com.evil.org/cover.jpg')
assert not allowed_bgg_cover_url('https://evil.com/cover.jpg')
assert allowed_bgg_cover_url('https://cf.geekdo-images.com/cover.jpg')
xml=b'<items><item type="boardgame" id="123"><name type="primary" value="Coup"/><minplayers value="2"/><maxplayers value="6"/><minplaytime value="15"/><maxplaytime value="20"/><image>https://cf.geekdo-images.com/sample.png</image><link type="boardgamecategory" value="Card Game"/></item></items>'
with patch('game_autofill.requests.get') as getter:
 response=Mock(status_code=200,content=xml);response.raise_for_status.return_value=None
 getter.return_value=response
 details,error=game_details(123,'fake-test-token')
 assert error is None and details['players']=='2–6' and details['duration']=='15–20'
 assert 'Карткова' in details['tags']
 assert getter.call_args.kwargs['headers']['Authorization']=='Bearer fake-test-token'
print('PASS: provider parsing, bearer authorization, SSRF-resistant cover host, no-key fallback')
