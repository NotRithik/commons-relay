/** Commons signed-message encoding; intentionally distinct from JCS/RFC 8785.
 * It matches the Python core for the supported safe-integer/string/object subset.
 */
import { createHash, createPrivateKey, createPublicKey, sign } from 'node:crypto';

export function scalarString(value) {
  if (typeof value !== 'string' || value.length > 32768) throw new Error('INVALID_STRING');
  for (let i=0;i<value.length;i++) {
    const c=value.charCodeAt(i);
    if (c>=0xd800 && c<=0xdbff) { const next=value.charCodeAt(++i);if (!(next>=0xdc00 && next<=0xdfff)) throw new Error('INVALID_STRING'); }
    else if (c>=0xdc00 && c<=0xdfff) throw new Error('INVALID_STRING');
  }
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
function compareKeys(a,b) {
  const aa=Array.from(a,c=>c.codePointAt(0)),bb=Array.from(b,c=>c.codePointAt(0));
  for(let i=0;i<Math.min(aa.length,bb.length);i++) if(aa[i]!==bb[i]) return aa[i]-bb[i];
  return aa.length-bb.length;
}
export function canonical(value) {
  const seen=new Set();
  function encode(v,depth=0) {
    if(depth>16) throw new Error('MESSAGE_TOO_DEEP');
    if(v===null)return 'null';
    if(typeof v==='string')return scalarString(v);
    if(typeof v==='boolean')return v?'true':'false';
    if(typeof v==='number') {
      if(!Number.isSafeInteger(v))throw new Error('UNSUPPORTED_JSON_NUMBER');
      return String(Object.is(v,-0)?0:v);
    }
    if(typeof v!=='object' || v===undefined || seen.has(v))throw new Error('INVALID_JSON_VALUE');
    seen.add(v);let result;
    if(Array.isArray(v)) {
      if(v.length>256 || Object.keys(v).length!==v.length)throw new Error('INVALID_ARRAY');
      result='['+v.map(x=>encode(x,depth+1)).join(',')+']';
    } else {
      const proto=Object.getPrototypeOf(v);
      if(proto!==Object.prototype && proto!==null)throw new Error('NONPLAIN_OBJECT');
      const keys=Object.keys(v).sort(compareKeys);
      if(keys.length>128 || Reflect.ownKeys(v).length!==keys.length)throw new Error('INVALID_OBJECT');
      result='{'+keys.map(k=>{
        const descriptor=Object.getOwnPropertyDescriptor(v,k);
        if(!descriptor || !Object.hasOwn(descriptor,'value'))throw new Error('ACCESSOR_NOT_ALLOWED');
        return scalarString(k)+':'+encode(descriptor.value,depth+1);
      }).join(',')+'}';
    }
    seen.delete(v);return result;
  }
  const bytes=Buffer.from(encode(value),'utf8');
  if(bytes.length>65536)throw new Error('MESSAGE_TOO_LARGE');
  return bytes;
}
export function envelopeSigner(privateKey) {
  const key=typeof privateKey==='object' && privateKey.type==='private'?privateKey:createPrivateKey(privateKey);
  if(key.asymmetricKeyType!=='ed25519')throw new Error('ED25519_REQUIRED');
  const publicKey=createPublicKey(key).export({format:'der',type:'spki'}).subarray(-32);
  const keyId='ed25519:'+createHash('sha256').update(publicKey).digest('hex');
  return Object.freeze({publicKey:publicKey.toString('base64url'),keyId,
    sign(body) {return {key_id:keyId,body,signature:sign(null,canonical(body),key).toString('base64url')};}});
}
