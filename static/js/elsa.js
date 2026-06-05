$(document).ready(function() {
    // Some pages (e.g. the redesigned Bundle Hub) no longer have a carousel.
    // Bail out early if it's absent so this script doesn't throw and abort.
    var carouselInner = $('.carousel-inner')[0];
    if (!carouselInner) {
        return;
    }
    var carousel_width = carouselInner.scrollWidth;
    var card_width = $('.carousel-item').first().outerWidth(true);
    var scroll_pos = 0;

    $('.carousel-control-prev').on('click', function() {
        if (scroll_pos >= 0) {
            scroll_pos -= card_width;
        } else {
            scroll_pos = 0;
        }
        
        $('.carousel-inner').animate({ scrollLeft: scroll_pos }, 600);
    
    });
    
    $('.carousel-control-next').on('click', function() {
        if (scroll_pos < (carousel_width - (card_width * 3))) {
            scroll_pos += card_width;
        } else {
            scroll_pos = carousel_width - (card_width * 3);
        }
    
        $('.carousel-inner').animate({ scrollLeft: scroll_pos }, 600);
    });
});