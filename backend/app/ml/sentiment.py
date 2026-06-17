import re
from typing import List, Dict, Any

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS


def _get_analyzer():
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        return SentimentIntensityAnalyzer()
    except Exception:
        return None


def _fallback_scores(text: str) -> Dict[str, float]:
    lower = str(text or '').lower()
    positive_terms = {'gain', 'gains', 'rise', 'rises', 'bullish', 'beat', 'beats', 'surge', 'up'}
    negative_terms = {'drop', 'drops', 'fall', 'falls', 'bearish', 'miss', 'misses', 'slump', 'down'}
    pos = sum(1 for term in positive_terms if term in lower)
    neg = sum(1 for term in negative_terms if term in lower)
    total = max(pos + neg, 1)
    compound = (pos - neg) / total
    return {
        'pos': max(compound, 0.0),
        'neg': abs(min(compound, 0.0)),
        'neu': 1.0 if pos == 0 and neg == 0 else 0.0,
        'compound': float(compound),
    }


def analyze_sentiment(text: str) -> Dict[str, float]:
    analyzer = _get_analyzer()
    if analyzer is None:
        return _fallback_scores(text)
    return analyzer.polarity_scores(text)


def sentiment_compound(text: str) -> float:
    return float(analyze_sentiment(text).get('compound', 0.0))


def aggregate_article_sentiments(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    stopwords = set(ENGLISH_STOP_WORDS)
    agg = {'count': 0, 'compound_sum': 0.0, 'pos': 0, 'neg': 0, 'neu': 0, 'keywords': {}}
    word_re = re.compile(r"\b[a-zA-Z]{3,}\b")
    for art in articles:
        txt = ' '.join(filter(None, [art.get('title', ''), art.get('description', ''), art.get('content', '')]))
        if not txt:
            continue
        s = analyze_sentiment(txt)
        agg['count'] += 1
        agg['compound_sum'] += s['compound']
        if s['compound'] >= 0.05:
            agg['pos'] += 1
        elif s['compound'] <= -0.05:
            agg['neg'] += 1
        else:
            agg['neu'] += 1
        # keyword extraction: simple frequency of non-stopwords
        words = [w.lower() for w in word_re.findall(txt)]
        for w in words:
            if w in stopwords:
                continue
            agg['keywords'][w] = agg['keywords'].get(w, 0) + 1
    # compute averages and top keywords
    if agg['count'] > 0:
        agg['avg_compound'] = agg['compound_sum'] / agg['count']
    else:
        agg['avg_compound'] = 0.0
    # top keywords
    kw = sorted(agg['keywords'].items(), key=lambda x: x[1], reverse=True)[:10]
    agg['top_keywords'] = [k for k, _ in kw]
    return agg
