/* Raster fallbacks are rendered from the committed SVGs; no graphics library or remote assets. */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require(process.argv[2] || process.env.PLAYWRIGHT_MODULE || "playwright");
const directory = path.resolve(__dirname, "../ai-berkshire-web/static/brand");

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({deviceScaleFactor:1});
    const sizes = [16,32,48];
    const images = [];
    for (const size of [...sizes,180]) {
      const apple = size === 180;
      const source = fs.readFileSync(path.join(directory, apple ? "mark.svg" : "favicon.svg"), "utf8");
      await page.setViewportSize({width:size,height:size});
      await page.setContent(`<style>html,body{margin:0;width:100%;height:100%;background:${apple ? "#163b40" : "transparent"}}svg{display:block;width:100%;height:100%}</style>${source}`);
      const buffer = await page.screenshot({omitBackground:!apple});
      fs.writeFileSync(path.join(directory, apple ? "apple-touch-icon.png" : `favicon-${size}.png`), buffer);
      if (!apple) images.push(buffer);
    }
    const header = Buffer.alloc(6 + 16 * sizes.length);
    header.writeUInt16LE(1,2);
    header.writeUInt16LE(sizes.length,4);
    let offset = header.length;
    images.forEach((buffer,index) => {
      const entry = 6 + 16 * index;
      header.writeUInt8(sizes[index],entry);
      header.writeUInt8(sizes[index],entry + 1);
      header.writeUInt16LE(1,entry + 4);
      header.writeUInt16LE(32,entry + 6);
      header.writeUInt32LE(buffer.length,entry + 8);
      header.writeUInt32LE(offset,entry + 12);
      offset += buffer.length;
    });
    fs.writeFileSync(path.join(directory,"favicon.ico"),Buffer.concat([header,...images]));
    console.log("brand_icons=generated ico=16,32,48 apple=180");
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
