import assert from 'node:assert/strict';
import { test } from 'node:test';
import { generateKeyPairSync, createPublicKey, verify } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { canonical,envelopeSigner } from '../../adapters/pi/canonical.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));

test('Python and JavaScript signed encodings match on Unicode and control characters',()=>{
  const samples=[{z:1,a:2},[null,true,false,0,-1],{label:'caf\u00e9 \ud83d\ude80',content:'\u007f\n\t\b\r\f\u2028'},
    {'\ufffd':1,'\ud83d\ude00':2,'A':3}, {amount:'340282366920938463463374607431768211455'}];
  for(const sample of samples){
    const p=spawnSync('python3',['-c','import sys,json;from commons_relay.codec import canonical;sys.stdout.buffer.write(canonical(json.load(sys.stdin)))'],{input:JSON.stringify(sample),cwd:root});
    assert.equal(p.status,0,p.stderr.toString());assert.deepEqual(canonical(sample),p.stdout);
  }
});
test('unsafe integers and floating point are rejected',()=>{
  for(const number of [1.2,NaN,Infinity,2**53])assert.throws(()=>canonical({number}));
});
test('unpaired surrogates rejected',()=>{for(const value of ['\ud800','\udc00','x\ud800y'])assert.throws(()=>canonical(value));});
test('accessors and nonplain objects rejected without invoking getters',()=>{
  let called=false;const value={};Object.defineProperty(value,'x',{enumerable:true,get(){called=true;return 1;}});
  assert.throws(()=>canonical(value));assert.equal(called,false);assert.throws(()=>canonical(new Date()));
});
test('cyclic and sparse arrays rejected',()=>{const a=[];a.push(a);assert.throws(()=>canonical(a));assert.throws(()=>canonical(new Array(2)));});
test('object symbol keys are rejected',()=>{assert.throws(()=>canonical({[Symbol('hidden')]:1}));});
test('size and depth caps',()=>{assert.throws(()=>canonical('x'.repeat(40000)));let value=null;for(let i=0;i<20;i++)value=[value];assert.throws(()=>canonical(value));});
test('Ed25519 envelope verifies with independent Node primitive',()=>{
  const {privateKey,publicKey}=generateKeyPairSync('ed25519');const signer=envelopeSigner(privateKey);const env=signer.sign({goal:'synthetic fixture'});
  assert.ok(verify(null,canonical(env.body),publicKey,Buffer.from(env.signature,'base64url')));
  assert.equal(Buffer.from(signer.publicKey,'base64url').length,32);assert.ok(signer.keyId.startsWith('ed25519:'));
  assert.equal(verify(null,canonical({goal:'changed'}),publicKey,Buffer.from(env.signature,'base64url')),false);
});
test('other key algorithms rejected',()=>{const {privateKey}=generateKeyPairSync('ec',{namedCurve:'prime256v1'});assert.throws(()=>envelopeSigner(privateKey));});
