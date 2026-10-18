const MM_TO_PX = 2.8346;

const Utils = {
    makeDraggable(el, onStop) {
        el.onmousedown = (e) => {
            if (e.target.tagName === 'BUTTON') return;
            let isResizing = (e.target.className === 'resizer');
            let startX = e.clientX, startY = e.clientY;
            let startW = el.offsetWidth, startH = el.offsetHeight;
            let startL = el.offsetLeft, startT = el.offsetTop;

            document.onmousemove = (e) => {
                if (isResizing) {
                    el.style.width = Math.max(20, startW + e.clientX - startX) + 'px';
                    el.style.height = Math.max(20, startH + e.clientY - startY) + 'px';
                } else {
                    el.style.left = (startL + e.clientX - startX) + 'px';
                    el.style.top = (startT + e.clientY - startY) + 'px';
                }
            };

            document.onmouseup = () => {
                document.onmousemove = null;
                document.onmouseup = null;
                if (onStop) onStop();
            };
        };
    },

    openTab(id, event) {
        document.getElementById('app-shell')?.classList.toggle('explorer-active', id === 'tab-explore');
        document.querySelectorAll('.tab-content, .tab').forEach(el => el.classList.remove('active'));
        document.getElementById(id).classList.add('active');
        const tab = event?.target?.closest('[data-tab-target]') || event?.currentTarget;
        tab?.classList?.add('active');
    },

    formatJson(obj) {
        return JSON.stringify(obj, null, 2);
    }
};
