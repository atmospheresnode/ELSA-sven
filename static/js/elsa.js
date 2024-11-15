$(document).ready(function() {
    // bundle carousel
    var carousel_width = $('.carousel-inner')[0].scrollWidth;
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