function o(e){const a={className:"attr",begin:/"(\\.|[^\\"\r\n])*"(?=\s*:)/,relevance:1.01},t={match:/[{}[\],:]/,className:"punctuation",relevance:0},n=["true","false","null"],c={scope:"literal",beginKeywords:n.join(" ")};return{name:"JSON",keywords:{literal:n},contains:[a,t,e.QUOTE_STRING_MODE,c,e.C_NUMBER_MODE,e.C_LINE_COMMENT_MODE,e.C_BLOCK_COMMENT_MODE],illegal:"\\S"}}export{o as default};
