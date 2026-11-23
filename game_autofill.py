"""Verified, opt-in game enrichment providers for Styloteka.

BGG, YouTube and Brave keys stay server side. Only URLs returned by the trusted
providers are suggested; an editor must select a result before publication.
"""
import html
import logging
import os
import re
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import quote, urlencode, urlparse

import requests

BGG_ENDPOINT = 'https://boardgamegeek.com/xmlapi2/'
YOUTUBE_ENDPOINT = 'https://www.googleapis.com/youtube/v3/search'
BRAVE_ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'
HEADERS = {'User-Agent': 'StylotekaBot/5.3.45 (board game catalogue; admin-controlled)'}

CATEGORY_UA = {
    'Card Game': 'Карткова', 'Dice': 'Кубики', 'Fantasy': 'Фентезі',
    'Science Fiction': 'Наукова фантастика', 'Animals': 'Тварини',
    'Economic': 'Економічна', 'Exploration': 'Дослідження',
    'Fighting': 'Битви', 'Medieval': 'Середньовіччя',
    'Adventure': 'Пригоди', 'Civilization': 'Цивілізація',
    'City Building': 'Містобудування', 'Puzzle': 'Головоломка',
    'Party Game': 'Вечірка', 'Educational': 'Навчальна',
    'Negotiation': 'Переговори', 'Racing': 'Перегони',
    'Trains': 'Потяги', 'Murder/Mystery': 'Детективна',
    'Children\'s Game': 'Дитяча', 'Humor': 'Гумор',
}


def _normalize(s):
    return re.sub(r'[^\w]+', '', str(s or '').casefold(), flags=re.UNICODE)


def _external_url(value, domains=None):
    try:
        parsed = urlparse(str(value or '').strip())
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return ''
        host = parsed.hostname.lower()
        if domains and not any(host == d or host.endswith('.' + d) for d in domains):
            return ''
        return parsed.geturl() if len(parsed.geturl()) <= 1300 else ''
    except (ValueError, TypeError):
        return ''


def _bgg_request(path, params, token):
    if not token:
        return None, 'bgg_not_configured'
    try:
        resp = requests.get(BGG_ENDPOINT + path,
            headers={**HEADERS, 'Authorization': 'Bearer ' + token},
            params=params, timeout=12)
        if resp.status_code in (202, 429):
            return None, 'bgg_busy'
        if resp.status_code in (401, 403):
            return None, 'bgg_token_invalid'
        resp.raise_for_status()
        if len(resp.content) > 2_000_000:
            return None, 'bgg_response_too_large'
        # ElementTree does not fetch external entities. BGG XML is parsed only,
        # never transformed into HTML without escaping on the client.
        return ET.fromstring(resp.content), None
    except (requests.RequestException, ET.ParseError, ValueError):
        logging.warning('BGG API unavailable, endpoint=%s', path)
        return None, 'bgg_unavailable'


def _primary_name(item):
    names = list(item.findall('name'))
    primary = next((n for n in names if n.get('type') == 'primary'), None)
    chosen = primary if primary is not None else (names[0] if names else None)
    return chosen.get('value', '') if chosen is not None else ''


def search_games(query, token):
    query = str(query or '').strip()
    if not 2 <= len(query) <= 100:
        return {'error': 'invalid_query'}
    root, error = _bgg_request('search', {'query':query, 'type':'boardgame'}, token)
    items = []
    if root is not None:
        for item in root.findall('item')[:60]:
            if item.get('type') != 'boardgame':
                continue
            raw_id = item.get('id', '')
            if not raw_id.isdecimal():
                continue
            name = _primary_name(item)
            names = [str(n.get('value', '')) for n in item.findall('name')]
            if not name:
                continue
            score = max((SequenceMatcher(None, _normalize(query), _normalize(n)).ratio() for n in names), default=0)
            exact = any(_normalize(query) == _normalize(n) for n in names)
            if exact: score += 1
            year = item.find('yearpublished')
            items.append({'id':int(raw_id), 'name':name[:160],
                'year': (year.get('value') if year is not None else '') or '',
                '_score':score})
    items.sort(key=lambda x:(-x['_score'], x['name']))
    items = [{k:v for k,v in x.items() if k!='_score'} for x in items[:8]]
    return {'candidates': items, 'provider':'BoardGameGeek', 'provider_error':error,
        'configured':bool(token), 'search_links': search_links(query)}


def search_links(query):
    q = str(query or '').strip()[:100]
    return {
        'bgg': 'https://boardgamegeek.com/geeksearch.php?' + urlencode({'action':'search','q':q}),
        'youtube': 'https://www.youtube.com/results?' + urlencode({'search_query':q + ' настільна гра огляд правила'}),
        'rules': 'https://www.google.com/search?' + urlencode({'q':q + ' настільна гра правила pdf'}),
    }


def _attr_number(item, field):
    element = item.find(field)
    raw = element.get('value', '') if element is not None else ''
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 0


def game_details(bgg_id, token):
    if not str(bgg_id).isdigit() or int(bgg_id) <= 0:
        return None, 'invalid_id'
    root, error = _bgg_request('thing', {'id':int(bgg_id)}, token)
    if error:
        return None, error
    item = root.find('item') if root is not None else None
    if item is None or item.get('type') != 'boardgame':
        return None, 'not_found'
    title = _primary_name(item)
    players_min = _attr_number(item, 'minplayers')
    players_max = _attr_number(item, 'maxplayers')
    time_min = _attr_number(item, 'minplaytime')
    time_max = _attr_number(item, 'maxplaytime')
    playing_time = _attr_number(item, 'playingtime')
    age = _attr_number(item, 'minage')
    categories_en = [n.get('value', '') for n in item.findall('link') if n.get('type') == 'boardgamecategory'][:4]
    categories = [CATEGORY_UA.get(n, n) for n in categories_en if n]
    player_label = (f'{players_min}–{players_max}' if players_min and players_max and players_min != players_max
                    else str(players_min or players_max or ''))
    duration = (f'{time_min}–{time_max}' if time_min and time_max and time_min != time_max
                else str(time_min or time_max or playing_time or ''))
    tags = []
    if player_label: tags.append(f'{player_label} гравців')
    if duration: tags.append(f'{duration} хв')
    tags.extend(categories[:2])
    # Only factual structured fields. Never paste the copyrighted BGG description.
    sentences = []
    if title and player_label: sentences.append(f'Настільна гра «{title}» для {player_label} гравців.')
    elif title: sentences.append(f'Настільна гра «{title}».')
    if duration: sentences.append(f'Орієнтовна тривалість партії — {duration} хв.')
    if categories: sentences.append(f'Тематика: {", ".join(categories[:3])}.')
    if age: sentences.append(f'Рекомендований вік: від {age} років.')
    full_image = (item.findtext('image') or '').strip()
    thumb = (item.findtext('thumbnail') or '').strip()
    cover = _external_url(full_image, ['geekdo-images.com']) or _external_url(thumb, ['geekdo-images.com'])
    url = f'https://boardgamegeek.com/boardgame/{int(bgg_id)}'
    return {'id':int(bgg_id), 'name':title, 'players':player_label, 'duration':duration,
            'min_age':age or None, 'categories':categories, 'tags':', '.join(tags),
            'description':' '.join(sentences), 'cover_url':cover, 'bgg_url':url,
            'year':(_attr_number(item,'yearpublished') or None), 'source':'BoardGameGeek'}, None


def _youtube_videos(title, api_key):
    if not api_key:
        return [], 'youtube_not_configured'
    try:
        response = requests.get(YOUTUBE_ENDPOINT, headers=HEADERS, params={
            'key':api_key,'part':'snippet','type':'video','q':f'{title} настільна гра правила огляд',
            'maxResults':5,'relevanceLanguage':'uk','safeSearch':'moderate'}, timeout=12)
        if not response.ok:
            return [], 'youtube_unavailable'
        videos=[]
        for item in response.json().get('items', []):
            vid = (item.get('id') or {}).get('videoId', '')
            if not re.fullmatch(r'[\w-]{11}', vid): continue
            snippet = item.get('snippet') or {}
            videos.append({'title':html.unescape(str(snippet.get('title') or 'Огляд'))[:160],
                           'channel':html.unescape(str(snippet.get('channelTitle') or ''))[:80],
                           'url':'https://www.youtube.com/watch?v='+vid})
        return videos[:4], None
    except (ValueError, requests.RequestException):
        logging.warning('YouTube API unavailable')
        return [], 'youtube_unavailable'


def _rule_results(title, brave_key):
    if not brave_key:
        return [], 'rules_search_not_configured'
    try:
        response = requests.get(BRAVE_ENDPOINT,
            headers={**HEADERS, 'X-Subscription-Token':brave_key, 'Accept':'application/json'},
            params={'q':f'"{title}" настільна гра правила rulebook pdf', 'count':8,
                    'search_lang':'uk','country':'UA'}, timeout=12)
        if not response.ok: return [], 'rules_search_unavailable'
        results=[]
        for entry in ((response.json().get('web') or {}).get('results') or []):
            link = _external_url(entry.get('url'))
            if not link: continue
            label = html.unescape(str(entry.get('title') or 'Правила'))[:160]
            # Human must pick, so don't imply that the result is an official rulebook.
            if link not in {r['url'] for r in results}:
                results.append({'title':label, 'url':link})
        return results[:4], None
    except (ValueError, requests.RequestException):
        logging.warning('Brave search unavailable')
        return [], 'rules_search_unavailable'


def preview_game(bgg_id, bgg_token, youtube_key='', brave_key=''):
    details, error = game_details(bgg_id, bgg_token)
    if error:
        return {'error':error}
    title = details['name']
    videos, youtube_error = _youtube_videos(title, youtube_key)
    rules, rules_error = _rule_results(title, brave_key)
    return {'game':details, 'videos':videos, 'rules':rules,
            'provider_errors':{'youtube':youtube_error, 'rules':rules_error},
            'search_links':search_links(title)}


def allowed_bgg_cover_url(raw):
    """Only HTTPS BGG image-CDN URLs; never fetch arbitrary URLs from users."""
    return _external_url(raw, ['cf.geekdo-images.com'])


def fetch_bgg_cover(raw):
    url = allowed_bgg_cover_url(raw)
    if not url: return None
    try:
        response = requests.get(url,headers=HEADERS,timeout=12,stream=True,allow_redirects=False)
        if response.status_code != 200: return None
        if int(response.headers.get('Content-Length') or 0) > 4_000_000: return None
        parts=[]; total=0
        for chunk in response.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > 4_000_000: return None
            parts.append(chunk)
        data=b''.join(parts)
        # Prevent uploading HTML/error pages masquerading as images.
        if data.startswith(b'\xff\xd8\xff'):
            return data, 'image/jpeg', 'bgg-cover.jpg'
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return data, 'image/png', 'bgg-cover.png'
        if data[:4]==b'RIFF' and data[8:12]==b'WEBP':
            return data, 'image/webp', 'bgg-cover.webp'
    except (ValueError, requests.RequestException):
        logging.warning('BGG cover could not be downloaded')
    return None
