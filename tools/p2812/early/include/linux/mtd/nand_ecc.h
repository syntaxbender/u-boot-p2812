#include <linux/mtd/mtd.h>
int nand_calculate_ecc(struct mtd_info *,const u_char *,u_char *);
int nand_correct_data(struct mtd_info *,u_char *,u_char *,u_char *);
