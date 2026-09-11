"""Human sentences from completed task receipts. Never invent missing fields."""
from __future__ import annotations
import struct

def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9007199254740991

def _text(value, limit=80):
    return isinstance(value, str) and 0 < len(value) <= limit

def _amount(value):
    return isinstance(value, str) and value.isdigit() and len(value) <= 39

def _commons_group_from_hex(hexdata):
    if not isinstance(hexdata, str) or len(hexdata) < 16 or len(hexdata) % 2:
        return None
    try:
        raw = bytes.fromhex(hexdata)
    except ValueError:
        return None
    header = b'COMNSM01'
    if not raw.startswith(header) or len(raw) < 8 + 32 + 4 + 4 + 8:
        return None
    at = 8 + 32
    members, threshold, value = struct.unpack_from('<IIq', raw, at)
    if not (1 <= members <= 256 and 1 <= threshold <= members and 0 <= value <= 10**12):
        return None
    return {'member_count': members, 'threshold': threshold, 'value': value}

_FAILED = {
    'WALLET_OPERATION_FAILED': 'The wallet operation failed before submission. Nothing was sent.',
    'PROGRAM_BINARY_INVALID': 'This file is not a deployable LEZ program. Pack the compiled ELF with the pinned compatibility kernel before reviewing a new deployment.',
    'WALLET_HAS_UNRECONCILED_OPERATION': 'An earlier wallet action still needs to be checked. This request was not paid. Resolve that action before trying again.',
    'OWNER_OR_REQUESTER_CANCELED': 'This action was canceled. Nothing was sent.',
    'PREPARATION_FAILED': 'The task could not be prepared. Nothing was sent or paid.',
    'AUTHORIZATION_EXPIRED': 'Approval expired before sending. Nothing was paid.',
    'DOWNSTREAM_PEER_UNRESPONSIVE': 'The other agent did not answer in time. Nothing was paid.',
    'DOWNSTREAM_TASK_HANDSHAKE_TIMEOUT': 'The other agent replied but did not accept the task in time. Nothing was paid.',
    'A2A_REMOTE_ERROR_32004': 'That task already finished, so there are no more live updates.',
    'A2A_REMOTE_ERROR_32002': 'That task cannot be canceled now.',
    'REMOTE_TASK_NOT_READY_TO_FOLLOW': 'The other agent has not accepted that task yet. Try again in a moment.',
    'FOLLOW_TARGET_PEER_MISMATCH': 'That task belongs to a different agent.',
}

def result_plain(skill, result, *, state='completed', error=None) -> str:
    if state in ('failed', 'rejected'):
        if isinstance(error, str) and error in _FAILED:
            return _FAILED[error]
        return 'This action did not finish' + (f' ({error}).' if isinstance(error, str) and error else '.')
    if state == 'canceled':
        return 'This action was canceled. Nothing was sent.'
    if state != 'completed' or not isinstance(result, dict):
        return ''
    if result.get('omitted') is True:
        return 'The recorded result is large. Open the task details to read it.'
    if skill == 'wallet.balance' and _amount(result.get('balance')):
        block = f' Recorded at block {result["block"]}.' if _count(result.get('block')) else ''
        return f'Recorded wallet balance: {result["balance"]} testnet units.{block}'
    if skill == 'wallet.history' and isinstance(result.get('operations'), list):
        n = len(result['operations'])
        if n == 0:
            return 'No wallet activity is recorded for this agent yet.'
        confirmed = sum(1 for row in result['operations'][:64] if isinstance(row, dict) and row.get('state') == 'confirmed')
        extra = f' {confirmed} confirmed.' if confirmed else ''
        return f'{n} wallet action{"s" if n != 1 else ""} recorded.{extra}'
    if skill == 'wallet.send' and result.get('private') is True and isinstance(result.get('transaction_hash'), str):
        block = f' Confirmed at block {result["block_id"]}.' if _count(result.get('block_id')) else ''
        return f'Private payment sent.{block}'
    if skill == 'storage.list' and isinstance(result.get('files'), list):
        n = len(result['files'])
        return 'No saved files were found in this agent’s file vault.' if n == 0 else f'{n} saved file{"s" if n != 1 else ""} in this agent’s file vault.'
    if skill == 'storage.upload' and _count(result.get('bytes')) and _text(result.get('address'), 128):
        return f'Encrypted file stored: {result["bytes"]} bytes.'
    if skill == 'storage.download' and result.get('authenticated') is True and _count(result.get('bytes')):
        return f'Downloaded and verified: {result["bytes"]} bytes.'
    if skill == 'storage.share' and (result.get('shared') is True or result.get('state') == 'acknowledged'):
        return 'The file was shared with the chosen recipient.'
    if skill == 'messaging.send' and result.get('state') == 'acknowledged':
        return 'Message delivered to the recipient.'
    if skill == 'messaging.create_group' and _text(result.get('group_id'), 128):
        return 'Group created and invitations sent.'
    if skill == 'messaging.join' and result.get('joined') is True:
        return 'Joined the group.'
    if skill == 'messaging.inbox' and isinstance(result.get('messages'), list):
        n = len(result['messages'])
        return 'No recent messages from other agents.' if n == 0 else f'{n} recent message{"s" if n != 1 else ""} from other agents.'
    if skill == 'agent.discover' and isinstance(result.get('agents'), list):
        n = len(result['agents'])
        names = []
        for row in result['agents'][:6]:
            if not isinstance(row, dict):
                continue
            card = row.get('card') if isinstance(row.get('card'), dict) else {}
            name = card.get('name') or card.get('description')
            if _text(name, 60):
                names.append(name)
        found = 'No other agents were found on that topic.' if n == 0 else f'Found {n} other agent{"s" if n != 1 else ""}.'
        return found if not names else f'{found} {", ".join(names)}.'
    if skill == 'agent.card':
        card = result.get('card') if isinstance(result.get('card'), dict) else result
        if isinstance(card, dict) and isinstance(card.get('skills'), list):
            n = len(card['skills'])
            name = card.get('name') if _text(card.get('name'), 60) else ''
            listed = f'{n} advertised tool{"s" if n != 1 else ""}'
            return f'{name}’s public card lists {listed}.' if name else f'This agent’s public card lists {listed}.'
        if isinstance(card, dict) and ('name' in card or 'skills' in card):
            return 'This agent’s public card was loaded.'
    if skill == 'agent.ping' and (result.get('status') == 'responding' or result.get('reachable') is True or result.get('online') is True):
        ms = result.get('round_trip_ms')
        extra = f' Reply took {ms} ms.' if _count(ms) else ''
        return 'The other agent answered. It is online right now.' + extra
    if skill == 'agent.ping' and (result.get('status') == 'no-reply' or result.get('reachable') is False or result.get('online') is False):
        return 'The other agent did not answer. Nothing was paid.'
    if skill == 'agent.task':
        paid = result.get('paid_amount')
        artifacts = result.get('artifacts')
        if _amount(paid) and isinstance(artifacts, list):
            pay = 'Nothing was paid.' if paid == '0' else f'Paid {paid} testnet unit{"s" if paid != "1" else ""}.'
            listed = None
            if artifacts and isinstance(artifacts[0], dict):
                parts = artifacts[0].get('parts')
                data = parts[0].get('data') if isinstance(parts, list) and parts and isinstance(parts[0], dict) else None
                if isinstance(data, dict) and isinstance(data.get('skills'), list):
                    listed = len(data['skills'])
                elif isinstance(data, dict) and all(_count(data.get(k)) for k in ('words', 'characters', 'lines', 'utf8_bytes')):
                    return (f"The service counted {data['words']} words, {data['characters']} characters, "
                            f"{data['lines']} line{'s' if data['lines'] != 1 else ''} and {data['utf8_bytes']} UTF-8 bytes. {pay}")
                elif isinstance(data, dict) and data.get('provider') == 'Exa' and isinstance(data.get('results'), list):
                    count=len(data['results'])
                    return f"The service returned {count} search result{'s' if count != 1 else ''}. {pay}"
                elif isinstance(data, dict) and data.get('provider') == 'Exa MCP free plan' and isinstance(data.get('text'),str) and data['text'].strip():
                    return f"The service returned a web-search response from Exa. {pay}"
            if listed is not None:
                return f'The other agent listed {listed} tools. {pay}'
            return f'Service finished with {len(artifacts)} returned result{"s" if len(artifacts) != 1 else ""}. {pay}'
    if skill == 'program.query':
        block = f' at block {result["block"]}' if _count(result.get('block')) else ''
        decoded = result.get('decoded_group') if isinstance(result.get('decoded_group'), dict) else None
        if not (decoded and _count(decoded.get('value')) and _count(decoded.get('member_count'))):
            decoded = _commons_group_from_hex(result.get('data_borsh_hex'))
        if decoded and _count(decoded.get('value')) and _count(decoded.get('member_count')):
            threshold = decoded['threshold'] if _count(decoded.get('threshold')) else '?'
            return (f'Commons group state{block}: value {decoded["value"]}, '
                    f'{decoded["member_count"]} members, threshold {threshold}.')
        return f'Program state was read{block}.'
    if skill == 'meta.skills' and isinstance(result.get('skills'), list):
        return f'This agent has {len(result["skills"])} tools available.'
    if skill == 'meta.status':
        wallet = result.get('wallet') if isinstance(result.get('wallet'), dict) else {}
        balance = wallet.get('balance') if _amount(wallet.get('balance')) else result.get('balance')
        extra = f' Balance {balance} testnet units.' if _amount(balance) else ''
        storage = result.get('storage_usage') if isinstance(result.get('storage_usage'), dict) else {}
        files = storage.get('file_count')
        files_txt = f' {files} stored files.' if _count(files) else ''
        return f'Current agent status loaded.{extra}{files_txt}'
    if skill == 'meta.configure' and isinstance(result.get('applied'), dict) and result['applied']:
        return 'The agent setting was updated.'
    if skill == 'agent.subscribe':
        if result.get('already_finished') is True:
            return 'That task already finished. There are no more live updates.'
        if result.get('subscribed') is True:
            return 'Now following that task for updates.'
        return 'Asked the other agent for live updates on that task.'
    if skill == 'agent.cancel':
        status = result.get('status') if isinstance(result.get('status'), dict) else {}
        unpaid = False
        meta = result.get('metadata') if isinstance(result.get('metadata'), dict) else {}
        for body in meta.values():
            if isinstance(body, dict) and body.get('paymentState') == 'unpaid':
                unpaid = True
        if status.get('state') == 'TASK_STATE_CANCELED' and unpaid:
            return 'The other agent canceled that unpaid task. Nothing was paid.'
        if status.get('state') == 'TASK_STATE_CANCELED':
            return 'The other agent canceled that task.'
        return 'Cancel was requested for that task.'
    return ''

def describe_tasks(tasks) -> str:
    lines = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        text = result_plain(task.get('skill'), task.get('result'), state=task.get('state', ''), error=task.get('error'))
        if text:
            lines.append(text)
    if not lines:
        return ''
    if len(lines) == 1:
        return lines[0]
    return ' '.join(f'{index}. {line}' for index, line in enumerate(lines, 1))


def service_result_text(result, maximum_bytes=5500):
    """A bounded readable copy of returned content, not a fresh model answer."""
    if not isinstance(result,dict) or not isinstance(result.get('artifacts'),list):return ''
    sections=[]
    for artifact in result['artifacts'][:4]:
        if not isinstance(artifact,dict) or not isinstance(artifact.get('parts'),list):continue
        for part in artifact['parts'][:4]:
            data=part.get('data') if isinstance(part,dict) else None
            if not isinstance(data,dict):continue
            if data.get('provider')=='Exa MCP free plan' and isinstance(data.get('text'),str):
                import re
                blocks=re.split(r'(?m)^Title: ',data['text'].strip())[1:]
                excerpts=[]
                for block in blocks[:5]:
                    lines=block.splitlines()
                    title=lines[0][:200] if lines else ''
                    url=next((line[5:][:1200] for line in lines if line.startswith('URL: ')), '')
                    if not title or not url:continue
                    marker = 'Highlights:' if 'Highlights:' in block else 'Excerpt:'
                    highlight = block.split(marker,1)[1] if marker in block else ''
                    highlight=' '.join(line.strip().lstrip('#').strip() for line in highlight.splitlines() if line.strip() not in ('', '...', title))
                    excerpts.append(title+'\n'+url+('\nExcerpt: '+highlight[:420] if highlight else ''))
                if excerpts:
                    sections.append(str(len(excerpts))+' linked excerpts in the saved response:\n\n'+'\n\n'.join(excerpts))
                elif data['text'].strip():
                    sections.append(data['text'].strip())
                if data.get('truncated') is True:
                    sections.insert(0,'The provider truncated this response. Some requested results or page text are missing; no replacement search was made.')
            elif data.get('provider')=='Exa' and isinstance(data.get('results'),list):
                for row in data['results'][:5]:
                    if not isinstance(row,dict):continue
                    fields=[row.get('title'),row.get('url'),row.get('excerpt')]
                    sections.append('\n'.join(v for v in fields if isinstance(v,str) and v.strip()))
    text='\n\n'.join(section for section in sections if section)
    if not text:return ''
    encoded=text.encode('utf8')
    if len(encoded)>maximum_bytes:
        text=encoded[:maximum_bytes].decode('utf8',errors='ignore')+'\n[Response excerpt; the full response remains in the recorded task.]'
    return text
