netcdf sample {
dimensions:
    time = 3 ;
    lat = 2 ;
    lon = 2 ;
variables:
    float temperature(time, lat, lon) ;
        temperature:units = "K" ;
    float lat(lat) ;
        lat:units = "degrees_north" ;
    float lon(lon) ;
        lon:units = "degrees_east" ;
    int time(time) ;
        time:units = "hours since 2024-01-01 00:00:00" ;
data:
 lat = 10, 20 ;
 lon = 30, 40 ;
 time = 0, 1, 2 ;
 temperature =
   290, 291, 292, 293,
   294, 295, 296, 297,
   298, 299, 300, 301 ;
}